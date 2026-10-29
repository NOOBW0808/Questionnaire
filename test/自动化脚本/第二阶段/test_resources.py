import uuid
import hashlib
from datetime import datetime, timezone
from copy import deepcopy

from bson import ObjectId, BSON, json_util
import pytest

from conftest import submit_answer


def test_resource_version_restore_and_published_stability(ctx):
    a = ctx.alice
    r, tag = a.resource()
    s, st = a.survey()
    imp, st = a.import_to(s['surveyId'], st, r)
    qid = imp['questionId']; path = '/surveys/' + s['surveyId']; sid = ObjectId(s['surveyId'])
    published = a.call('POST', path + '/publish', {}, etag=st).json()['data']
    assert published['state'] == 'PUBLISHED' and published['deadlineAt'] is None
    snap = BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1}))
    first = submit_answer(ctx.bob, published['publicCode'], [{'questionId': qid, 'value': '18'}])
    first_id = ObjectId(first['responseId'])
    before_response = BSON.encode(ctx.db.responses.find_one({'_id': first_id}))
    started = a.call('POST', f'/public-surveys/{published["publicCode"]}/attempts', {}, status=201)
    pending, pending_tag = started.json()['data'], started.headers['ETag']
    content = a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content']
    content['title'] = '今年年龄'; content['validation']['minValue'] = '21'
    new = a.call('POST', f'/question-resources/{r["resourceId"]}/versions',
        {'baseVersionId': r['questionVersionId'], 'content': content}, etag=tag, key=uuid.uuid4().hex, status=201)
    v2 = new.json()['data']
    restored = a.call('POST', f'/question-resources/{r["resourceId"]}/restorations',
        {'baseVersionId': v2['questionVersionId'], 'restoreFromVersionId': r['questionVersionId']},
        etag=new.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    history = a.get(f'/question-resources/{r["resourceId"]}/versions')[0]['items']
    assert len(history) == 3
    v3 = next(x for x in history if x['questionVersionId'] == restored['questionVersionId'])
    assert v3['restoredFromVersionId'] == r['questionVersionId'] and v3['parentVersionId'] == v2['questionVersionId']
    assert a.get(path)[0]['draft']['questions'][0]['title'] == '年龄'
    _, st = a.get(path)
    bound = a.call('PUT', path + '/questions/' + qid + '/binding',
        {'resourceId': r['resourceId'], 'questionVersionId': v2['questionVersionId']}, etag=st)
    republished = a.call('POST', path + '/publish', {}, etag=bound.headers['ETag']).json()['data']
    assert republished['publishedVersion'] == 2 and republished['state'] == 'PUBLISHED' and republished['deadlineAt'] is None
    # Submit the pending v1 attempt only AFTER v2 was published.
    saved = a.call('PUT', f'/attempts/{pending["attemptId"]}/answers',
        {'answers': [{'questionId': qid, 'value': '20'}]}, etag=pending_tag)
    old = a.call('POST', f'/attempts/{pending["attemptId"]}/submit', {}, etag=saved.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    response = ctx.db.responses.find_one({'_id': ObjectId(old['responseId'])})
    assert response['publishedVersion'] == 1 and 'respondentUserId' not in response
    assert response['answers'][0]['value'].to_decimal() == 20
    current = a.call('POST', f'/public-surveys/{published["publicCode"]}/attempts', {}, status=201)
    attempt = current.json()['data']; atag = current.headers['ETag']
    assert attempt['publishedVersion'] == 2
    rejected = a.call('PUT', f'/attempts/{attempt["attemptId"]}/answers',
        {'answers': [{'questionId': qid, 'value': '20'}]}, etag=atag, status=422)
    assert rejected.json()['error']['code'] == 'NUMBER_RANGE'
    assert ctx.db.responses.count_documents({'surveyId': sid}) == 2
    saved = a.call('PUT', f'/attempts/{attempt["attemptId"]}/answers',
        {'answers': [{'questionId': qid, 'value': '30'}]}, etag=atag)
    result = a.call('POST', f'/attempts/{attempt["attemptId"]}/submit', {}, etag=saved.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    assert ctx.db.responses.find_one({'_id': ObjectId(result['responseId'])})['publishedVersion'] == 2
    public = a.get('/public-surveys/' + published['publicCode'])[0]['definition']
    assert 'reuseBinding' not in public['questions'][0]
    assert BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1})) == snap
    assert BSON.encode(ctx.db.responses.find_one({'_id': first_id})) == before_response


def test_adoption_preserves_source_and_legacy_execution(ctx):
    a = ctx.alice; s, tag = a.survey(); path = '/surveys/' + s['surveyId']; sid = ObjectId(s['surveyId'])
    added = a.call('POST', path + '/questions', {'type': 'SINGLE', 'title': '参加吗',
        'options': [{'label': '是'}, {'label': '否'}]}, etag=tag, status=201)
    original = deepcopy(added.json()['data']['draft']['questions'][0]); qid = original['questionId']
    a.call('POST', path + '/publish', {}, etag=added.headers['ETag'])
    snap = BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid}))
    pending = a.call('POST', f'/public-surveys/{s["publicCode"]}/attempts', {}, status=201)
    before = ctx.db.surveys.find_one({'_id': sid}); _, tag = a.get(path)
    raw_question = deepcopy(before['draft']['questions'][0]); raw_bson = BSON.encode(raw_question)
    # Independent oracle: never call the application's canonical_hash.
    canonical = json_util.dumps(raw_question, json_options=json_util.CANONICAL_JSON_OPTIONS,
                               ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    expected_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    earliest = datetime.now(timezone.utc); key = uuid.uuid4().hex
    adopted = a.call('POST', path + '/questions/' + qid + '/adoption', {}, etag=tag, key=key, status=201)
    latest = datetime.now(timezone.utc); made = adopted.json()['data']
    stored = ctx.db.question_versions.find_one({'_id': ObjectId(made['questionVersionId'])})
    source_raw = stored['adoptionSource']
    assert source_raw['sourceKind'] == 'DRAFT_INSTANCE' and source_raw['surveyId'] == sid
    assert source_raw['questionId'] == qid and source_raw['definitionVersion'] == before['definitionVersion']
    assert source_raw['controlRevision'] == before['controlRevision']
    # BSON datetimes have millisecond precision; truncate the lower bound accordingly.
    earliest = earliest.replace(microsecond=(earliest.microsecond // 1000) * 1000)
    assert earliest <= source_raw['extractedAt'] <= latest
    assert source_raw['sourceQuestionHash'] == expected_hash
    assert BSON.encode(source_raw['sourceQuestionSnapshot']) == raw_bson
    replay = a.call('POST', path + '/questions/' + qid + '/adoption', {}, etag=tag, key=key).json()['data']
    assert replay['resourceId'] == made['resourceId'] and replay['replayed']
    source_path = f'/question-resources/{made["resourceId"]}/versions/{made["questionVersionId"]}'
    source = a.get(source_path)[0]['adoptionSource']
    assert source['sourceQuestionSnapshot'] == original and source['sourceQuestionHash'] == expected_hash
    now, tag = a.get(path); assert now['draft']['questions'][0]['options'] == original['options']
    a.call('DELETE', path + '/questions/' + qid, etag=tag, status=204)
    saved = a.call('PUT', f'/attempts/{pending.json()["data"]["attemptId"]}/answers',
        {'answers': [{'questionId': qid, 'value': original['options'][0]['optionId']}]}, etag=pending.headers['ETag'])
    result = a.call('POST', f'/attempts/{pending.json()["data"]["attemptId"]}/submit', {},
        etag=saved.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
    response = ctx.db.responses.find_one({'_id': ObjectId(result['responseId'])})
    assert response['publishedVersion'] == 1 and response['answers'][0]['value'] == original['options'][0]['optionId']
    assert a.get(source_path)[0]['adoptionSource'] == source
    assert BSON.encode(ctx.db.survey_versions.find_one({'surveyId': sid})) == snap


def test_working_draft_freeze_and_permissions(ctx):
    a = ctx.alice
    r, tag = a.resource(fixed=False)
    rpath = '/question-resources/' + r['resourceId']
    c = a.get(rpath)[0]['workingDraft']; c['title'] = '稿2'
    rr = a.call('PATCH', rpath + '/draft', {'content': c}, etag=tag)
    fixed = a.call('POST', rpath + '/versions', {}, etag=rr.headers['ETag'], key=uuid.uuid4().hex, status=201)
    assert fixed.json()['data']['questionVersionId']
    ctx.bob.call('GET', rpath, status=404)
    a.call('PATCH', rpath + '/draft', {'content': c}, etag=tag, status=409)


def test_bound_content_guard_and_invalid_rebind_rollback(ctx):
    a = ctx.alice; r, _ = a.resource(kind='SINGLE')
    s, st = a.survey(); imp, st = a.import_to(s['surveyId'], st, r)
    path = '/surveys/' + s['surveyId']; qid = imp['questionId']; sid = ObjectId(s['surveyId'])
    def baseline():
        view, etag = a.get(path)
        return {'survey': BSON.encode(ctx.db.surveys.find_one({'_id': sid})), 'view': view, 'etag': etag,
                'usages': [BSON.encode(x) for x in ctx.db.question_usages.find({'surveyId': sid}).sort('_id', 1)],
                'snapshots': [BSON.encode(x) for x in ctx.db.survey_versions.find({'surveyId': sid}).sort('_id', 1)]}
    before = baseline()
    a.call('PATCH', path + '/questions/' + qid, {'title': '直接篡改'}, etag=st, status=409)
    assert baseline() == before
    opt = before['view']['draft']['questions'][0]['options'][0]['optionId']
    rule = a.call('PUT', path + '/questions/' + qid + '/jump-rules',
        {'rules': [{'priority': 1, 'operator': 'EQ', 'value': opt, 'target': 'END'}]}, etag=st)
    a.call('POST', path + '/publish', {}, etag=rule.headers['ETag'])
    other, _ = a.resource(kind='SINGLE', title='其他内容')
    before = baseline(); key = uuid.uuid4().hex
    a.call('PUT', path + '/questions/' + qid + '/binding',
        {'resourceId': other['resourceId'], 'questionVersionId': other['questionVersionId']},
        etag=before['etag'], key=key, status=422)
    assert baseline() == before  # complete instance/map/rules/revisions/projections/snapshots
    assert ctx.db.operation_keys.count_documents({'actorId': ObjectId(a.uid),
        'keyHash': hashlib.sha256(key.encode()).hexdigest()}) == 0


@pytest.mark.parametrize('kind', ['SINGLE', 'MULTIPLE', 'TEXT', 'NUMBER'])
def test_four_types_can_be_reused(ctx, kind):
    a = ctx.alice
    content = {'type': kind, 'title': '复用执行-' + kind, 'validation': {}}
    if kind in ('SINGLE', 'MULTIPLE'):
        content['options'] = [{'label': '是'}, {'label': '否'}]
    if kind == 'MULTIPLE': content['validation'] = {'exactSelections': 2}
    if kind == 'TEXT': content['validation'] = {'minLength': 2, 'maxLength': 4}
    if kind == 'NUMBER': content['validation'] = {'minValue': '0', 'maxValue': '150', 'integerOnly': True}
    r = a.call('POST', '/question-resources', {'content': content, 'fixed': True}, key=uuid.uuid4().hex, status=201).json()['data']
    version = a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]
    for _ in range(2):
        s, st = a.survey(); sid = ObjectId(s['surveyId'])
        imp, st = a.import_to(s['surveyId'], st, r); qid = imp['questionId']
        q = a.get('/surveys/' + s['surveyId'])[0]['draft']['questions'][0]
        assert all(q[k] == version['content'][k] for k in ('title', 'type', 'validation'))
        assert q['required'] is False and q['jumpRules'] == []
        binding = q['reuseBinding']
        assert binding['resourceId'] == r['resourceId'] and binding['questionVersionId'] == r['questionVersionId']
        assert binding['contentHash'] == version['contentHash']
        mapping = {x['semanticOptionId']: x['localOptionId'] for x in binding['optionMap']}
        assert len(mapping) == len(binding['optionMap']) == len(set(mapping.values()))
        assert q.get('options', []) == [{'optionId': mapping[o['semanticOptionId']], 'label': o['label']} for o in version['content'].get('options', [])]
        if kind == 'SINGLE': bad = [('unknown_option', 'OPTION_INVALID')]; good = q['options'][0]['optionId']
        elif kind == 'MULTIPLE': bad = [([q['options'][0]['optionId']], 'SELECTION_COUNT')]; good = [x['optionId'] for x in q['options']]
        elif kind == 'TEXT': bad = [('一', 'TEXT_LENGTH'), ('一二三四五', 'TEXT_LENGTH')]; good = '正确'
        else: bad = [('-1', 'NUMBER_RANGE'), ('151', 'NUMBER_RANGE'), ('20.5', 'INTEGER_REQUIRED')]; good = '20'
        a.call('POST', '/surveys/' + s['surveyId'] + '/publish', {}, etag=st)
        snap = ctx.db.survey_versions.find_one({'surveyId': sid, 'publishedVersion': 1})
        assert snap['definition']['questions'][0]['reuseBinding']['questionVersionId'] == ObjectId(r['questionVersionId'])
        started = a.call('POST', f'/public-surveys/{s["publicCode"]}/attempts', {}, status=201)
        attempt = started.json()['data']; aid = attempt['attemptId']; tag = started.headers['ETag']
        for invalid, code in bad:
            before = BSON.encode(ctx.db.attempts.find_one({'_id': ObjectId(aid)}))
            response = a.call('PUT', f'/attempts/{aid}/answers', {'answers': [{'questionId': qid, 'value': invalid}]}, etag=tag, status=422)
            assert response.json()['error']['code'] == code
            assert ctx.db.responses.count_documents({'surveyId': sid}) == 0
            assert BSON.encode(ctx.db.attempts.find_one({'_id': ObjectId(aid)})) == before
        saved = a.call('PUT', f'/attempts/{aid}/answers', {'answers': [{'questionId': qid, 'value': good}]}, etag=tag)
        result = a.call('POST', f'/attempts/{aid}/submit', {}, etag=saved.headers['ETag'], key=uuid.uuid4().hex, status=201).json()['data']
        stored = ctx.db.responses.find_one({'_id': ObjectId(result['responseId'])})
        assert stored['publishedVersion'] == 1 and stored['answers'][0]['questionId'] == qid
        value = stored['answers'][0]['value']
        assert (str(value.to_decimal()) if kind == 'NUMBER' else value) == good
    assert ctx.db.question_usages.count_documents({'resourceId': ObjectId(r['resourceId']), 'snapshotVersion': 0}) == 2
