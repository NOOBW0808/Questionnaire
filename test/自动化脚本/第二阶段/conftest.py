"""Real HTTP and randomized test databases; never loads a business database."""
import os
from pathlib import Path
import sys
import threading
import uuid

import pytest
import requests
from werkzeug.serving import make_server, WSGIRequestHandler

CODE = Path(__file__).resolve().parents[3] / "code"
sys.path.insert(0, str(CODE))


class Quiet(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@pytest.fixture(scope='session')
def service():
    name = 'questionnaire_p2_test_' + uuid.uuid4().hex
    previous = {k: os.environ.get(k) for k in ('MONGODB_DATABASE', 'CURSOR_KEY')}
    os.environ['MONGODB_DATABASE'] = name
    from cryptography.fernet import Fernet
    os.environ['CURSOR_KEY'] = Fernet.generate_key().decode()
    from survey_app import create_app
    try:
        app = create_app()
    except Exception:
        from pymongo import MongoClient
        with MongoClient(os.getenv('MONGODB_URI')) as cleanup:
            cleanup.drop_database(name)  # Only the exact name allocated above.
        raise
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
    base = f'http://127.0.0.1:{server.server_port}'
    app.config['APP_ORIGIN'] = base
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, base
    finally:
        server.shutdown(); server.server_close(); thread.join(10)
        client = app.extensions['database'].client
        assert app.extensions['database'].db.name == name and name.startswith('questionnaire_p2_test_')
        if not os.environ.get('KEEP_TEST_DATABASE'):
            client.drop_database(name)
        client.close()
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Client:
    def __init__(self, base):
        self.base = base
        self.http = requests.Session()
        self.name = 'p2_' + uuid.uuid4().hex[:18]
        password = 'TestPass!2026'
        r = self.http.post(base + '/api/v1/auth/register', json={'username': self.name, 'password': password})
        assert r.status_code == 201, r.text
        r = self.http.post(base + '/api/v1/auth/login', json={'username': self.name, 'password': password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()['data']['csrfToken']
        self.uid = r.json()['data']['user']['userId']

    def call(self, method, path, data=None, etag=None, key=None, status=200, headers=None):
        h = {'Origin': self.base, 'X-CSRF-Token': self.csrf}
        if etag:
            h['If-Match'] = etag
        if key:
            h['Idempotency-Key'] = key
        h.update(headers or {})
        r = self.http.request(method, self.base + '/api/v1' + path, json=data if data is not None else {}, headers=h, timeout=40)
        assert r.status_code == status, f'{method} {path}: {r.status_code} {r.text}'
        return r

    def get(self, path):
        r = self.call('GET', path)
        return r.json()['data'], r.headers.get('ETag')

    def resource(self, title='年龄', fixed=True, kind='NUMBER', semantics=True):
        c = {'type': kind, 'title': title, 'validation': {}}
        if kind in ('SINGLE', 'MULTIPLE'):
            c['options'] = [{'label': '是'}, {'label': '否'}]
        if kind == 'NUMBER':
            c['validation'] = {'integerOnly': True, 'minValue': '0', 'maxValue': '150'}
        if semantics:
            c['semantics'] = {'measure': '周岁年龄', 'unit': '岁', 'timeBasis': '填写当日', 'population': '受访者'}
        r = self.call('POST', '/question-resources', {'content': c, 'fixed': fixed}, key=uuid.uuid4().hex, status=201)
        return r.json()['data'], r.headers['ETag']

    def survey(self, title='P2问卷'):
        r = self.call('POST', '/surveys', {'title': title, 'allowRepeat': True, 'anonymous': True}, status=201)
        return r.json()['data'], r.headers['ETag']

    def import_to(self, sid, etag, r, consent=False):
        data = {'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}
        if consent:
            data['usageSummaryConsent'] = {'accepted': True, 'policyVersion': 'SUMMARY_V1'}
        response = self.call('POST', f'/surveys/{sid}/question-imports', data, etag=etag, key=uuid.uuid4().hex, status=201)
        return response.json()['data'], response.headers['ETag']


@pytest.fixture
def ctx(service):
    from survey_app.security import _rates, _lock
    with _lock:
        _rates.clear()
    app, base = service
    return type('Context', (), {'app': app, 'base': base, 'db': app.extensions['database'].db,
                                'alice': Client(base), 'bob': Client(base)})()


def submit_answer(client, public_code, entries):
    import uuid
    r = client.call('POST', f'/public-surveys/{public_code}/attempts', {}, status=201)
    a = r.json()['data']; tag = r.headers['ETag']
    r = client.call('PUT', f'/attempts/{a["attemptId"]}/answers', {'answers': entries}, etag=tag)
    r = client.call('POST', f'/attempts/{a["attemptId"]}/submit', {}, etag=r.headers['ETag'], key=uuid.uuid4().hex, status=201)
    return r.json()['data']
