import hashlib
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import allure
import pytest
from cryptography.fernet import Fernet
from werkzeug.serving import WSGIRequestHandler, make_server

TEST_ROOT = Path(__file__).resolve().parents[4]
ROOT = TEST_ROOT.parent / "code"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from support import Context, reset_rates

CATALOG = json.loads((TEST_ROOT / "测试步骤/原始规格/cases.json").read_text("utf-8"))
CASES = {c["id"]: c for c in CATALOG["cases"]}


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@pytest.fixture(scope="session")
def service():
    name = "questionnaire_test_" + uuid.uuid4().hex
    fields = {"MONGODB_DATABASE": name, "CURSOR_KEY": Fernet.generate_key().decode(), "COOKIE_SECURE": "false"}
    previous = {k: os.environ.get(k) for k in fields}
    os.environ.update(fields)
    from survey_app import create_app
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    base = f"http://127.0.0.1:{server.server_port}"
    app.config["APP_ORIGIN"] = base
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manifest = {"database": name, "baseUrl": base, "isolation": "random database; real HTTP; unchanged business source",
                "rateReset": "between cases and fixture auth only; rate-limit assertions use original limiter",
                "clock": "MongoDB server clock; exact equality not forced"}
    Path(os.environ.get("API_RUNTIME_JSON", TEST_ROOT / "api-runtime.json")).write_text(json.dumps(manifest, indent=2), "utf-8")
    try:
        yield app, base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        database = app.extensions["database"]
        assert database.db.name == name and name.startswith("questionnaire_test_")
        if not os.getenv("KEEP_TEST_DATABASE"):
            database.client.drop_database(name)
        database.client.close()
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def ctx(service, request):
    reset_rates()
    app, base = service
    context = Context(app, base)
    cid = request.node.callspec.params.get("case_id") if hasattr(request.node, "callspec") else "TC06-M01-AUTO"
    spec = CASES.get(cid)
    allure.dynamic.label("parentSuite", "在线问卷系统")
    allure.dynamic.label("execution", "api")
    allure.dynamic.label("caseId", cid)
    if spec:
        allure.dynamic.title(cid + " " + spec["title"])
        allure.dynamic.suite(spec["module"])
        allure.dynamic.sub_suite("API 自动化")
        allure.dynamic.description("前置、输入、步骤和预期见附件。所有业务动作通过本机真实 HTTP；数据库只用于核对持久化及明确说明的故障/会话准备。")
        allure.attach(json.dumps(spec, ensure_ascii=False, indent=2), "原始测试规格", allure.attachment_type.JSON)
    try:
        yield context
    finally:
        context.evidence("HTTP 请求与响应（凭据已脱敏）", context.trace)
        context.evidence("本例问卷持久化摘要", [
            {"surveyId": sid, "responses": context.db.responses.count_documents({"surveyId": sid}),
             "guards": context.db.participation_guards.count_documents({"surveyId": sid}),
             "versions": context.db.survey_versions.count_documents({"surveyId": sid}),
             "attempts": context.db.attempts.count_documents({"surveyId": sid})} for sid in context.survey_ids])


def pytest_sessionfinish(session, exitstatus):
    """Enforce that every catalog API ID has exactly one collected scenario."""
    expected = {c["id"] for c in CATALOG["cases"] if c["execution"] == "api"}
    actual = [i.callspec.params["case_id"] for i in session.items if hasattr(i, "callspec") and "case_id" in i.callspec.params]
    if not session.config.getoption("-k") and not session.config.getoption("--collect-only"):
        assert set(actual) == expected and len(actual) == len(expected), "API catalog coverage mismatch"
