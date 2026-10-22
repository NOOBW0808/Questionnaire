"""Run the submitted API scenarios against the sibling code tree."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import uuid


TEST = Path(__file__).resolve().parent
CODE = TEST.parent / "code"
SCENARIOS = TEST / "测试步骤/自动化脚本/test/api"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-only", action="store_true", help="Collect 130 scenarios without connecting to MongoDB")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/?replicaSet=survey-rs"))
    parser.add_argument("--allure", help="Optional Allure 2 CLI executable for generating a fresh API-only report")
    parser.add_argument("--filter", help="Optional pytest -k expression; subset execution is not full coverage")
    parser.add_argument("--keep-database", action="store_true", help="Keep only this run's randomized test database")
    args = parser.parse_args()
    if not (CODE / "survey_app/__init__.py").is_file():
        parser.error("Missing sibling code/survey_app")
    allure_cli = None
    if args.allure:
        allure_cli = shutil.which(args.allure)
        if not allure_cli:
            parser.error("Allure executable was not found")

    env = os.environ.copy()
    # The submitted code wins over any ambient project path.
    env["PYTHONPATH"] = str(CODE)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [sys.executable, "-X", "utf8", "-m", "pytest", "-p", "allure_pytest.plugin",
               "-c", "pytest.ini", str(SCENARIOS), "-q", "--tb=short"]
    if args.filter:
        command.extend(["-k", args.filter])
    if args.collect_only:
        return subprocess.run(command + ["--collect-only"], cwd=TEST, env=env).returncode

    env["MONGODB_URI"] = args.mongo_uri
    from pymongo import MongoClient
    # Refuse unavailable or standalone MongoDB before starting the test run.
    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        hello = client.admin.command("hello")
        if not (hello.get("setName") and hello.get("isWritablePrimary")):
            parser.error("MongoDB must be a writable primary in a replica set")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    folder = TEST / "runs" / run_id
    folder.mkdir(parents=True)
    env["API_RUNTIME_JSON"] = str(folder / "runtime.json")
    env.pop("KEEP_TEST_DATABASE", None)
    if args.keep_database:
        env["KEEP_TEST_DATABASE"] = "1"
    command.extend(["--alluredir", str(folder / "allure-results"), "--junitxml", str(folder / "junit.xml")])
    print("Testing sibling code; new API results:", folder, flush=True)
    with (folder / "pytest.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=TEST, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        code = process.wait()
    report_code = None
    if allure_cli:
        report_code = subprocess.run([allure_cli, "generate", str(folder / "allure-results"),
                                      "--clean", "-o", str(folder / "allure-report")], cwd=TEST).returncode
    (folder / "run-summary.json").write_text(json.dumps({
        "kind": "fresh-api-only-run", "runId": run_id, "pytestExitCode": code,
        "allureExitCode": report_code, "filter": args.filter, "manualResultsIncluded": False,
        "codeDirectory": "code", "historicalEvidenceOverwritten": False
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code or report_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
