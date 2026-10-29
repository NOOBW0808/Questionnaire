import uuid
from concurrent.futures import ThreadPoolExecutor

from bson import ObjectId, BSON

from conftest import submit_answer


def share(ctx, r):
    return ctx.alice.call('PUT', f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}/grants',
        {'username': ctx.bob.name}, headers={'If-None-Match': '*'})


def test_shared_version_scope_consent_fork_and_anonymous_whitelist(ctx):
    a, b = ctx.alice, ctx.bob
    r, tag = a.resource()
    share(ctx, r)
    path = '/question-resources/' + r['resourceId']
    assert b.get(path)[0]['ownership'] == 'SHARED'
    assert 'workingDraft' not in b.get(path)[0]
    b.call('PATCH', path + '/draft', {'content': {}}, etag=tag, status=404)
    b.call('GET', path + '/versions/' + r['questionVersionId'] + '/grants', status=404)
    c = a.get(path + '/versions/' + r['questionVersionId'])[0]['content']; c['title'] = '新私有版'
    new = a.call('POST', path + '/versions', {'baseVersionId': r['questionVersionId'], 'content': c}, etag=tag, key=uuid.uuid4().hex, status=201).json()['data']
    b.call('GET', path + '/versions/' + new['questionVersionId'], status=404)
    s, st = b.survey()
    data = {'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}
    b.call('POST', f'/surveys/{s["surveyId"]}/question-imports', data, etag=st, key=uuid.uuid4().hex, status=409)
    assert ctx.db.question_usages.count_documents({'surveyId': ObjectId(s['surveyId'])}) == 0
    imp, st = b.import_to(s['surveyId'], st, r, consent=True)
    usage = a.get(path + '/usages')[0]
    assert usage['totalVisible'] == 1 and usage['items'][0]['ownership'] == 'OTHER'
    assert set(usage['items'][0]) == {'surveyId','surveyTitle','ownership','resourceId','questionVersionId','publishedVersion','usageStage','surveyStatus','instanceCount'}
    a.call('GET', '/surveys/' + s['surveyId'], status=404)
    forked = b.call('POST', path + '/versions/' + r['questionVersionId'] + '/forks', {}, key=uuid.uuid4().hex, status=201).json()['data']
    owned = ctx.db.question_resources.find_one({'_id': ObjectId(forked['resourceId'])})
    assert str(owned['ownerId']) == b.uid
    v = b.get(f'/question-resources/{forked["resourceId"]}/versions/{forked["questionVersionId"]}')[0]
    assert v['derivedFrom'] == {'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}
    a.call('GET', '/question-resources/' + forked['resourceId'], status=404)


def test_bank_updates_do_not_propagate_and_delete_keeps_history(ctx):
    a = ctx.alice; r, rt = a.resource()
    made = a.call('POST', '/question-banks', {'name': '常用'}, key=uuid.uuid4().hex, status=201)
    bank = made.json()['data']; bp = '/question-banks/' + bank['bankId']
    member = a.call('PUT', bp + '/members/' + r['resourceId'], {'questionVersionId': r['questionVersionId']}, etag=made.headers['ETag'])
    s, st = a.survey(); imp, st = a.import_to(s['surveyId'], st, r); sid = ObjectId(s['surveyId'])
    path = '/surveys/' + s['surveyId']; qid = imp['questionId']
    pub = a.call('POST', path + '/publish', {}, etag=st).json()['data']
    assert pub['state'] == 'PUBLISHED' and pub['deadlineAt'] is None
    response = submit_answer(ctx.bob, s['publicCode'], [{'questionId': qid, 'value': '18'}])
    response_id = ObjectId(response['responseId'])
    pending = a.call('POST', f'/public-surveys/{s["publicCode"]}/attempts', {}, status=201)
    pending_id = pending.json()['data']['attemptId']
    snapshot_before = BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1}))
    response_before = BSON.encode(ctx.db.responses.find_one({'_id': response_id}))
    c = a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content']; c['title'] = '新版'
    v2 = a.call('POST', f'/question-resources/{r["resourceId"]}/versions',
        {'baseVersionId': r['questionVersionId'], 'content': c}, etag=rt, key=uuid.uuid4().hex, status=201).json()['data']
    member = a.call('PUT', bp + '/members/' + r['resourceId'], {'questionVersionId': v2['questionVersionId']}, etag=member.headers['ETag'])
    assert a.get(path)[0]['draft']['questions'][0]['reuseBinding']['questionVersionId'] == r['questionVersionId']
    assert BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1})) == snapshot_before
    def protected():
        return {'resource': BSON.encode(ctx.db.question_resources.find_one({'_id': ObjectId(r['resourceId'])})),
                'versions': [BSON.encode(x) for x in ctx.db.question_versions.find({'resourceId': ObjectId(r['resourceId'])}).sort('_id', 1)],
                'survey': BSON.encode(ctx.db.surveys.find_one({'_id': sid})),
                'snapshots': [BSON.encode(x) for x in ctx.db.survey_versions.find({'surveyId': sid}).sort('_id', 1)],
                'response': BSON.encode(ctx.db.responses.find_one({'_id': response_id})),
                'pending': BSON.encode(ctx.db.attempts.find_one({'_id': ObjectId(pending_id)}))}
    before = protected()
    ctx.bob.call('DELETE', bp, etag=member.headers['ETag'], status=404)
    assert protected() == before
    a.call('DELETE', bp, etag=member.headers['ETag'], status=204)
    a.call('GET', bp, status=404)
    assert protected() == before
    assert ctx.db.question_versions.count_documents({'resourceId': ObjectId(r['resourceId'])}) == 2
    saved = a.call('PUT', f'/attempts/{pending_id}/answers', {'answers': [{'questionId': qid, 'value': '20'}]}, etag=pending.headers['ETag'])
    result = a.call('POST', f'/attempts/{pending_id}/submit', {}, etag=saved.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    old = ctx.db.responses.find_one({'_id': ObjectId(result['responseId'])})
    assert old['publishedVersion'] == 1 and old['answers'][0]['value'].to_decimal() == 20
    fresh = submit_answer(a, s['publicCode'], [{'questionId': qid, 'value': '22'}])
    new = ctx.db.responses.find_one({'_id': ObjectId(fresh['responseId'])})
    assert new['publishedVersion'] == 1 and new['answers'][0]['value'].to_decimal() == 22
    assert BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1})) == snapshot_before
    assert BSON.encode(ctx.db.responses.find_one({'_id': response_id})) == response_before


def test_summary_close_reopen_and_usage_pagination(ctx):
    r, _ = ctx.alice.resource(); share(ctx, r)
    b = ctx.bob
    surveys=[]
    for i in range(2):
        s, st = b.survey('共享引用' + str(i)); b.import_to(s['surveyId'], st, r, consent=True); surveys.append(s)
    base = '/question-resources/' + r['resourceId'] + '/usages'
    page, _ = ctx.alice.get(base + '?limit=1')
    assert page['totalVisible'] == 2 and len(page['items']) == 1 and page['nextCursor']
    nextpage = ctx.alice.get(base + '?limit=1&cursor=' + page['nextCursor'])[0]
    assert nextpage['items'][0]['surveyId'] != page['items'][0]['surveyId']
    sid = surveys[0]['surveyId']; p = f'/surveys/{sid}/usage-summary-grants/{r["resourceId"]}'
    grant, tag = b.get(p)
    closed = b.call('PUT', p, {'enabled': False, 'policyVersion': 'SUMMARY_V1'}, etag=tag)
    assert ctx.alice.get(base)[0]['totalVisible'] == 1
    assert ctx.bob.get(base)[0]['totalVisible'] == 2
    _, st = b.get('/surveys/' + sid)
    b.call('POST', f'/surveys/{sid}/question-imports', {'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}, etag=st, key=uuid.uuid4().hex, status=409)
    b.call('PUT', p, {'enabled': True, 'policyVersion': 'SUMMARY_V1'}, etag=closed.headers['ETag'])
    assert ctx.alice.get(base)[0]['totalVisible'] == 2


def test_adopt_private_source_not_shared(ctx):
    a = ctx.alice; s, st = a.survey()
    p = '/surveys/' + s['surveyId']
    response = a.call('POST', p + '/questions', {'type': 'TEXT', 'title': '私有上下文'}, etag=st, status=201)
    q = response.json()['data']['draft']['questions'][0]
    made = a.call('POST', p + '/questions/' + q['questionId'] + '/adoption', {}, etag=response.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    share(ctx, made)
    shared = ctx.bob.get(f'/question-resources/{made["resourceId"]}/versions/{made["questionVersionId"]}')[0]
    assert 'adoptionSource' not in shared and 'createdBy' not in shared
