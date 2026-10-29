"""Eight formerly planned business cases: history, binding, banks and summaries."""
from copy import deepcopy
import uuid

import allure
from bson import BSON, ObjectId

from conftest import Client, submit_answer
from phase2_support import bank, bound, evidence, expect, pages, raw, scoped_state, version
from test_sharing import share


def test_history_pagination_and_shared_lineage_filter(ctx):
    a, b = ctx.alice, ctx.bob
    r, tag = a.resource()
    v2, tag = version(a, r, tag, lambda c: c.update(title='第二版'))
    v3, _ = version(a, v2, tag, lambda c: c.update(title='第三版'))
    path = '/question-resources/' + r['resourceId']
    share(ctx, r)
    expected = [v3['questionVersionId'], v2['questionVersionId'], r['questionVersionId']]
    for endpoint in ('versions', 'lineage'):
        rows = pages(a, path + '/' + endpoint)
        assert [x['questionVersionId'] for x in rows] == expected
        shared = pages(b, path + '/' + endpoint)
        assert [x['questionVersionId'] for x in shared] == [r['questionVersionId']]
        assert all('adoptionSource' not in x and 'createdBy' not in x for x in shared)
    cursor = a.get(path + '/versions?limit=1')[0]['nextCursor']
    expect(raw(b, 'GET', path + '/versions?cursor=' + cursor), 400, 'CURSOR_INVALID')
    expect(raw(a, 'GET', path + '/versions?cursor=broken'), 400, 'CURSOR_INVALID')
    for v in (v2, v3):
        expect(raw(b, 'GET', path + '/versions/' + v['questionVersionId']), 404, 'NOT_FOUND')


def test_explicit_rebind_option_mapping_preserves_execution(ctx):
    a = ctx.alice
    old, _ = a.resource(kind='SINGLE')
    new, _ = a.resource(title='新措辞', kind='SINGLE')
    s, tag = a.survey()
    imported, tag = a.import_to(s['surveyId'], tag, old)
    path = '/surveys/' + s['surveyId']
    q = a.get(path)[0]['draft']['questions'][0]
    rule = {'operator': 'EQ', 'value': q['options'][0]['optionId'], 'target': 'END', 'priority': 1}
    update = a.call('PUT', path + '/questions/' + q['questionId'] + '/jump-rules', {'rules': [rule]}, etag=tag)
    tail = a.call('POST', path + '/questions', {'type': 'TEXT', 'title': '后续题'}, etag=update.headers['ETag'], status=201)
    tag = tail.headers['ETag']
    c = a.get(f'/question-resources/{new["resourceId"]}/versions/{new["questionVersionId"]}')[0]['content']
    mapping = [{'semanticOptionId': x['semanticOptionId'], 'localOptionId': y['optionId']}
               for x, y in zip(c['options'], q['options'])]
    data = {k: new[k] for k in ('resourceId', 'questionVersionId')}
    for invalid in (mapping[:1], [mapping[0], mapping[0]]):
        before = scoped_state(ctx, s['surveyId'], [a.uid])
        expect(raw(a, 'PUT', path + '/questions/' + q['questionId'] + '/binding', {**data, 'optionMap': invalid}, tag), 422)
        assert scoped_state(ctx, s['surveyId'], [a.uid]) == before
    result = a.call('PUT', path + '/questions/' + q['questionId'] + '/binding', {**data, 'optionMap': mapping}, etag=tag)
    current = a.get(path)[0]['draft']['questions']
    assert current[0]['questionId'] == q['questionId']
    assert current[0]['jumpRules'][0]['value'] == q['options'][0]['optionId']
    assert current[0]['reuseBinding']['optionMap'] == mapping
    a.call('POST', path + '/publish', {}, etag=result.headers['ETag'])
    for index, visited in [(0, [q['questionId']]), (1, [q['questionId'], current[1]['questionId']])]:
        entries = [{'questionId': q['questionId'], 'value': q['options'][index]['optionId']}]
        if index == 1:
            entries.append({'questionId': current[1]['questionId'], 'value': ''})
        response = submit_answer(a, s['publicCode'], entries)
        saved = ctx.db.responses.find_one({'_id': ObjectId(response['responseId'])})
        assert saved['visitedQuestionIds'] == visited


def test_derive_and_bind_atomic_owner_and_source(ctx, monkeypatch):
    from survey_app import phase2_core
    a, b = ctx.alice, ctx.bob
    r, _ = a.resource(); share(ctx, r)
    s, tag = b.survey(); imp, tag = b.import_to(s['surveyId'], tag, r, consent=True)
    path = '/surveys/' + s['surveyId']
    c = b.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content']
    c['title'] = '接收者的年龄题'
    before = scoped_state(ctx, s['surveyId'], [a.uid, b.uid])
    original = phase2_core.sync_usages
    def stop(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('isolated derive projection fault')
    with monkeypatch.context() as patch:
        patch.setattr(phase2_core, 'sync_usages', stop)
        expect(raw(b, 'POST', path + '/questions/' + imp['questionId'] + '/derivations', {'content': c}, tag, uuid.uuid4().hex), 500)
    assert scoped_state(ctx, s['surveyId'], [a.uid, b.uid]) == before
    original_version = BSON.encode(ctx.db.question_versions.find_one({'_id': ObjectId(r['questionVersionId'])}))
    out = b.call('POST', path + '/questions/' + imp['questionId'] + '/derivations', {'content': c}, etag=tag, key=uuid.uuid4().hex, status=201).json()['data']
    resource = ctx.db.question_resources.find_one({'_id': ObjectId(out['resourceId'])})
    derived = ctx.db.question_versions.find_one({'_id': ObjectId(out['questionVersionId'])})
    assert resource['ownerId'] == resource['createdBy'] == ObjectId(b.uid)
    assert derived['versionNo'] == 1 and derived['parentVersionId'] is None
    assert derived['derivedFrom'] == {'resourceId': ObjectId(r['resourceId']), 'questionVersionId': ObjectId(r['questionVersionId'])}
    assert b.get(path)[0]['draft']['questions'][0]['reuseBinding']['resourceId'] == out['resourceId']
    assert BSON.encode(ctx.db.question_versions.find_one({'_id': ObjectId(r['questionVersionId'])})) == original_version
    expect(raw(a, 'GET', '/question-resources/' + out['resourceId']), 404)


def test_bank_rename_remove_multibank_and_pagination(ctx):
    a = ctx.alice; r, _ = a.resource()
    s, _, _ = bound(a, r)
    protected = scoped_state(ctx, s['surveyId'], [a.uid])
    first, ft = bank(a, '常用'); second, st = bank(a, '课程')
    first_res = a.call('PUT', first + '/members/' + r['resourceId'], {'questionVersionId': r['questionVersionId']}, etag=ft)
    a.call('PUT', second + '/members/' + r['resourceId'], {'questionVersionId': r['questionVersionId']}, etag=st)
    renamed = a.call('PATCH', first, {'name': '常用2'}, etag=first_res.headers['ETag'])
    assert a.get(first)[0]['name'] == '常用2'
    a.call('DELETE', first + '/members/' + r['resourceId'], etag=renamed.headers['ETag'])
    assert a.get(first)[0]['members'] == []
    assert a.get(second)[0]['members'] == [{'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}]
    r2, _ = a.resource('第二题')
    _, tag = a.get(second)
    a.call('PUT', second + '/members/' + r2['resourceId'], {'questionVersionId': r2['questionVersionId']}, etag=tag)
    assert {x['resourceId'] for x in pages(a, second, 'members')} == {r['resourceId'], r2['resourceId']}
    assert {x['bankId'] for x in pages(a, '/question-banks')} == {first.split('/')[-1], second.split('/')[-1]}
    for name in ('surveys', 'survey_versions', 'question_usages'):
        assert scoped_state(ctx, s['surveyId'], [a.uid])[name] == protected[name]


def test_grant_invalid_accounts_and_private_successors(ctx):
    a, b = ctx.alice, ctx.bob; r, tag = a.resource()
    path = f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}/grants'
    for name in ('missing_' + uuid.uuid4().hex[:12], a.name):
        expect(raw(a, 'PUT', path, {'username': name}, headers={'If-None-Match': '*'}), 422, 'RECIPIENT_INVALID')
    assert ctx.db.question_grants.count_documents({'resourceId': ObjectId(r['resourceId'])}) == 0
    first = a.call('PUT', path, {'username': b.name}, headers={'If-None-Match': '*'})
    expect(raw(a, 'PUT', path, {'username': b.name}, headers={'If-None-Match': '*'}), 428)
    a.call('PUT', path, {'username': b.name}, etag=first.headers['ETag'])
    assert ctx.db.question_grants.count_documents({'resourceId': ObjectId(r['resourceId'])}) == 1
    v2, _ = version(a, r, tag, lambda c: c.update(title='未共享v2'))
    expect(raw(b, 'GET', f'/question-resources/{r["resourceId"]}/versions/{v2["questionVersionId"]}'), 404)
    expect(raw(b, 'GET', path), 404)
    expect(raw(b, 'PUT', path, {'username': a.name}, headers={'If-None-Match': '*'}), 404)
    expect(raw(b, 'PATCH', '/question-resources/' + r['resourceId'] + '/draft', {'content': {'type': 'TEXT', 'title': '越权'}}, etag='"resource-0"'), 404)
    assert {x['questionVersionId'] for x in pages(b, '/question-resources/' + r['resourceId'] + '/versions')} == {r['questionVersionId']}


def test_three_actor_summary_visibility_and_visible_counts(ctx):
    a, b = ctx.alice, ctx.bob; c = Client(ctx.base)
    r, _ = a.resource(); share(ctx, r)
    a.call('PUT', f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}/grants', {'username': c.name}, headers={'If-None-Match': '*'})
    own, tag = a.survey(); a.import_to(own['surveyId'], tag, r)
    bs, tag = b.survey(); b.import_to(bs['surveyId'], tag, r, consent=True)
    cs, tag = c.survey(); c.import_to(cs['surveyId'], tag, r, consent=True)
    grant = f'/surveys/{cs["surveyId"]}/usage-summary-grants/{r["resourceId"]}'
    _, tag = c.get(grant); c.call('PUT', grant, {'enabled': False, 'policyVersion': 'SUMMARY_V1'}, etag=tag)
    path = '/question-resources/' + r['resourceId'] + '/usages'
    visible = pages(a, path)
    assert {x['surveyId'] for x in visible} == {own['surveyId'], bs['surveyId']}
    assert a.get(path)[0]['totalVisible'] == 2
    assert {x['surveyId'] for x in pages(c, path)} == {cs['surveyId']}
    assert c.get(path)[0]['totalVisible'] == 1
    fields = {'surveyId','surveyTitle','ownership','resourceId','questionVersionId','publishedVersion','usageStage','surveyStatus','instanceCount'}
    assert all(set(x) == fields for x in visible)
    expect(raw(a, 'GET', '/surveys/' + bs['surveyId']), 404)


def test_usage_stage_versions_and_duplicate_instances(ctx):
    a = ctx.alice; r, rt = a.resource(); v2, _ = version(a, r, rt, lambda c: c.update(title='第二版'))
    s, tag = a.survey(); first, tag = a.import_to(s['surveyId'], tag, r); _, tag = a.import_to(s['surveyId'], tag, r)
    path = '/surveys/' + s['surveyId']; pub = a.call('POST', path + '/publish', {}, etag=tag)
    reb = a.call('PUT', path + '/questions/' + first['questionId'] + '/binding',
                 {k: v2[k] for k in ('resourceId','questionVersionId')}, etag=pub.headers['ETag'])
    a.call('POST', path + '/publish', {}, etag=reb.headers['ETag'])
    base = '/question-resources/' + r['resourceId'] + '/usages'
    rows = pages(a, base)
    got = {(x['usageStage'], x['publishedVersion'], x['questionVersionId']): x['instanceCount'] for x in rows}
    assert got == {('DRAFT',None,r['questionVersionId']):1, ('DRAFT',None,v2['questionVersionId']):1,
        ('CURRENT_PUBLISHED',2,r['questionVersionId']):1, ('CURRENT_PUBLISHED',2,v2['questionVersionId']):1,
        ('HISTORICAL_PUBLISHED',1,r['questionVersionId']):2}
    for stage in ('DRAFT','CURRENT_PUBLISHED','HISTORICAL_PUBLISHED'):
        selected = pages(a, base + '?usageStage=' + stage)
        assert selected and all(x['usageStage'] == stage for x in selected)
    assert len(pages(a, base + '?questionVersionId=' + v2['questionVersionId'])) == 2


def test_stale_usage_projection_is_rechecked(ctx):
    a = ctx.alice; r, _ = a.resource(); s, imp, _ = bound(a, r)
    sid = ObjectId(s['surveyId']); base = '/question-resources/' + r['resourceId'] + '/usages'
    original = list(ctx.db.question_usages.find({'surveyId': sid}))
    assert len(original) == 2
    stale = deepcopy(original[0]); stale['_id'] = ObjectId(); stale['questionId'] = 'q_' + uuid.uuid4().hex
    ctx.db.question_usages.insert_one(stale)
    assert len(pages(a, base)) == 2
    assert a.get(base)[0]['totalVisible'] == 2
    assert all(x['instanceCount'] == 1 for x in pages(a, base))
    ctx.db.question_usages.delete_one({'_id': stale['_id']})
    ctx.db.question_usages.delete_many({'surveyId': sid})
    empty = a.get(base)[0]
    assert empty['items'] == [] and empty['totalVisible'] == 0
    assert empty['coverage'] == 'SELF_AND_AUTHORIZED_OTHERS'
    evidence('缺投影覆盖边界', {'actual': empty, 'limitation': '查询仅遍历使用投影；缺行需要显式重建，不自动扫描所有问卷'})
    # Rebuild using the existing projection function, only on this disposable fixture.
    from survey_app.phase2_core import sync_usages
    with ctx.app.app_context():
        sdoc = ctx.db.surveys.find_one({'_id': sid})
        sync_usages(sdoc, None)
        snap = ctx.db.survey_versions.find_one({'surveyId': sid})
        sync_usages({**sdoc, 'draft': snap['definition'], 'definitionVersion': snap['sourceDefinitionVersion']}, None, snap['publishedVersion'])
    assert len(pages(a, base)) == 2
