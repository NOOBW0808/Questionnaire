"""Tag existing API scripts; do not change tests, fixtures or business behavior."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import allure
import pytest

from .common import read, encoded

CATALOG = read(Path(os.environ['CYCLE_CATALOG']))
CASES = {c['id']: c for c in CATALOG['cases']}
NODES = {c['automation']['node']: c['id'] for c in CATALOG['cases']
         if c['phase'] == 2 and c['automation'].get('node')}
OBSERVATIONS = {}


def identify(item):
    if '第一阶段' in str(item.path):
        return item.callspec.params['case_id'] if hasattr(item, 'callspec') else 'TC06-M01-AUTO'
    return NODES[item.name]


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    cid = identify(item)
    spec = CASES[cid]
    OBSERVATIONS[cid] = {'caseId': cid, 'status': 'passed', 'actual': '',
                         'startedAt': stamp(), 'updatedAt': stamp(), 'reports': []}
    allure.dynamic.label('caseId', cid)
    allure.dynamic.label('cycleId', os.environ['CYCLE_ID'])
    allure.dynamic.label('phase', str(spec['phase']))
    allure.dynamic.label('execution', 'api')
    allure.dynamic.title(cid + ' ' + spec['title'])
    allure.dynamic.suite(spec['module'])
    allure.attach(encoded(spec).decode(), '本轮完整用例规格', allure.attachment_type.JSON)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    cid = identify(item)
    row = OBSERVATIONS.setdefault(cid, {'caseId': cid, 'status': 'broken', 'actual': '',
                                      'startedAt': stamp(), 'updatedAt': stamp(), 'reports': []})
    row['reports'].append({'when': report.when, 'outcome': report.outcome, 'duration': report.duration})
    row['updatedAt'] = stamp()
    if report.failed:
        row['status'] = 'failed' if report.when == 'call' and isinstance(call.excinfo.value, AssertionError) else 'broken'
        row['actual'] += '\n' + str(report.longrepr)
    elif report.skipped and row['status'] == 'passed':
        row['status'] = 'skipped'
        row['actual'] = str(report.longrepr)


@pytest.fixture(autouse=True)
def phase2_http_evidence(request, monkeypatch):
    """Capture synthetic test HTTP traffic, without credential values."""
    if '第二阶段' not in str(request.node.path):
        yield
        return
    import requests
    trace = []
    original = requests.Session.request

    def clean(value):
        if isinstance(value, dict):
            return {k: '[REDACTED]' if any(s in k.lower() for s in ('password', 'token', 'cookie', 'secret')) else clean(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    def wrapped(session, method, url, **kw):
        item = {'method': method, 'url': url, 'json': clean(kw.get('json'))}
        start = time.monotonic()
        try:
            response = original(session, method, url, **kw)
            item.update(statusCode=response.status_code, ETag=response.headers.get('ETag'))
            try:
                item['response'] = clean(response.json())
            except ValueError:
                item['response'] = response.text[:3000]
            return response
        except Exception as exc:
            item['error'] = str(exc)
            raise
        finally:
            item['seconds'] = time.monotonic() - start
            trace.append(item)
    monkeypatch.setattr(requests.Session, 'request', wrapped)
    try:
        yield
    finally:
        allure.attach(encoded(trace).decode(), '本轮HTTP请求响应（测试数据，凭据脱敏）', allure.attachment_type.JSON)


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ['CYCLE_OBSERVATIONS']).write_bytes(encoded({
        'cycleId': os.environ['CYCLE_ID'], 'pytestExitCode': exitstatus, 'results': OBSERVATIONS}))
