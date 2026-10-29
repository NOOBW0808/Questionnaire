"""Test-side helpers. All writes target fixture-allocated disposable databases."""
from copy import deepcopy
import uuid

import allure
from bson import BSON, ObjectId, json_util


def raw(actor, method, path, data=None, etag=None, key=None, headers=None):
    h = {'Origin': actor.base, 'X-CSRF-Token': actor.csrf}
    if etag:
        h['If-Match'] = etag
    if key:
        h['Idempotency-Key'] = key
    h.update(headers or {})
    return actor.http.request(method, actor.base + '/api/v1' + path, json=data or {}, headers=h, timeout=40)


def expect(response, status, code=None):
    assert response.status_code == status, (response.request.method, response.request.url, response.text)
    if code:
        assert response.json()['error']['code'] == code, response.text
    if status >= 400:
        assert response.json().get('requestId')
        assert 'data' not in response.json()
    return response


def version(actor, r, tag, change=None):
    c = deepcopy(actor.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content'])
    if change:
        change(c)
    out = actor.call('POST', '/question-resources/' + r['resourceId'] + '/versions',
        {'baseVersionId': r['questionVersionId'], 'content': c}, etag=tag, key=uuid.uuid4().hex, status=201)
    return out.json()['data'], out.headers['ETag']


def bound(actor, r, title='扩展测试', anonymous=True):
    s, tag = actor.survey(title)
    if not anonymous:
        out = actor.call('PATCH', '/surveys/' + s['surveyId'], {'anonymous': False}, etag=tag)
        tag = out.headers['ETag']
    imp, tag = actor.import_to(s['surveyId'], tag, r)
    pub = actor.call('POST', '/surveys/' + s['surveyId'] + '/publish', {}, etag=tag)
    return s, imp, pub.headers['ETag']


def target(r, surveys):
    return {'targets': [{'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}],
            'surveyIds': [s['surveyId'] for s in surveys]}


def bank(actor, name):
    r = actor.call('POST', '/question-banks', {'name': name}, key=uuid.uuid4().hex, status=201)
    return '/question-banks/' + r.json()['data']['bankId'], r.headers['ETag']


def pages(actor, path, field='items'):
    rows, cursors = [], set()
    url = path + ('&' if '?' in path else '?') + 'limit=1'
    while True:
        data = actor.get(url)[0]
        rows += data[field]
        cursor = data.get('nextCursor')
        if not cursor:
            break
        assert cursor not in cursors
        cursors.add(cursor)
        url = path + ('&' if '?' in path else '?') + 'limit=1&cursor=' + cursor
    return rows


def bson_rows(database, name, query):
    return [BSON.encode(x) for x in database[name].find(query).sort('_id', 1)]


def evidence(name, value):
    allure.attach(json_util.dumps(value, ensure_ascii=False, indent=2), name, allure.attachment_type.JSON)


def scoped_state(ctx, sid, owners):
    sid = ObjectId(sid)
    owners = [ObjectId(x) for x in owners]
    return {name: bson_rows(ctx.db, name, query) for name, query in {
        'surveys': {'_id': sid}, 'survey_versions': {'surveyId': sid},
        'question_usages': {'surveyId': sid}, 'usage_summary_grants': {'surveyId': sid},
        'question_resources': {'ownerId': {'$in': owners}},
        'question_versions': {'createdBy': {'$in': owners}},
        'operation_keys': {'actorId': {'$in': owners}},
    }.items()}
