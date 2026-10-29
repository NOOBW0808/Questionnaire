import uuid
from decimal import Decimal

from bson import ObjectId
import pytest

from conftest import submit_answer


def scenario(ctx, skip_latest=True):
    a = ctx.alice; r, tag = a.resource()
    surveys=[]
    for name in ('A', 'B'):
        s, st = a.survey(name); imp, st = a.import_to(s['surveyId'], st, r)
        s = a.call('POST', '/surveys/' + s['surveyId'] + '/publish', {}, etag=st).json()['data']
        surveys.append((s, imp['questionId']))
    s, q = surveys[0]
    submit_answer(a, s['publicCode'], [{'questionId': q, 'value': '18'}])
    submit_answer(a, s['publicCode'], [{'questionId': q, 'value': '20'}])
    submit_answer(ctx.bob, s['publicCode'], [{'questionId': q, 'value': '30'}])
    if skip_latest:
        submit_answer(ctx.bob, s['publicCode'], [{'questionId': q, 'value': None}])
    s, q = surveys[1]
    submit_answer(a, s['publicCode'], [{'questionId': q, 'value': '22'}])
    data = {'targets': [{'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}],
            'surveyIds': [s['surveyId'] for s, _ in surveys]}
    return r, tag, surveys, data


def test_latest_whole_distinct_accounts_and_anonymous_output(ctx):
    r, tag, surveys, data = scenario(ctx)
    result = ctx.alice.call('POST', '/question-statistics/cross-survey', data).json()['data']
    group = result['groups'][0]
    assert len(result['groups']) == 1
    assert group['answeredParticipationCount'] == 2 and group['distinctAnsweredAccountCount'] == 1
    assert group['average'] == '21'
    assert group['distribution']['items'] == [{'value': '20', 'count': 1}, {'value': '22', 'count': 1}]
    assert result['latestWholeResponseCount'] == 3
    text = str(result)
    assert ctx.alice.uid not in text and ctx.bob.uid not in text
    for forbidden in ('participationId', 'internalAccountId', 'respondentUserId', 'responseId', 'answers'):
        assert forbidden not in text


@pytest.mark.parametrize('damage', ['guard_missing', 'snapshot_missing', 'guard_wrong_survey', 'guard_invalid_account', 'questions_malformed'])
def test_integrity_failure_never_returns_partial_report(ctx, damage):
    _, _, surveys, data = scenario(ctx)
    sid = ObjectId(surveys[1][0]['surveyId'])
    response = ctx.db.responses.find_one({'surveyId': sid})
    if damage == 'guard_missing':
        ctx.db.participation_guards.delete_one({'_id': response['participationId']})
    elif damage == 'snapshot_missing':
        ctx.db.survey_versions.delete_one({'surveyId': sid})
    elif damage == 'guard_wrong_survey':
        ctx.db.participation_guards.update_one({'_id': response['participationId']}, {'$set': {'surveyId': ObjectId()}})
    elif damage == 'guard_invalid_account':
        ctx.db.participation_guards.update_one({'_id': response['participationId']}, {'$set': {'userId': 'illegal'}}, bypass_document_validation=True)
    else:
        ctx.db.survey_versions.update_one({'surveyId': sid}, {'$set': {'definition.questions': None}}, bypass_document_validation=True)
    response = ctx.alice.call('POST', '/question-statistics/cross-survey', data, status=500).json()
    assert response['error']['code'] == 'DATA_INTEGRITY_ERROR' and 'data' not in response
    assert 'details' not in response['error']


def test_merge_declaration_bins_and_unit_rejection(ctx):
    a = ctx.alice; r, tag = a.resource()
    c = a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content']
    c['title'] = '当前年龄'
    v2 = a.call('POST', f'/question-resources/{r["resourceId"]}/versions',
        {'baseVersionId': r['questionVersionId'], 'content': c}, etag=tag, key=uuid.uuid4().hex, status=201).json()['data']
    sids = []
    for version, values in [(r, ('18', '22')), (v2, ('30', '50'))]:
        survey, st = a.survey(); imported, st = a.import_to(survey['surveyId'], st, version)
        a.call('POST', '/surveys/' + survey['surveyId'] + '/publish', {}, etag=st)
        for actor, value in zip((a, ctx.bob), values):
            submit_answer(actor, survey['publicCode'], [{'questionId': imported['questionId'], 'value': value}])
        sids.append(survey['surveyId'])
    data = {'targets': [{'resourceId': r['resourceId'], 'questionVersionId': x['questionVersionId']} for x in (r, v2)], 'surveyIds': sids}
    grouped = a.call('POST', '/question-statistics/cross-survey', data).json()['data']
    assert grouped['latestWholeResponseCount'] == 4 and len(grouped['groups']) == 2
    expected = {r['questionVersionId']: ('20', [('18', 1), ('22', 1)]), v2['questionVersionId']: ('40', [('30', 1), ('50', 1)])}
    for group in grouped['groups']:
        mean, frequency = expected[group['questionVersionIds'][0]]
        assert group['answeredParticipationCount'] == 2 and group['distinctAnsweredAccountCount'] == 2
        assert group['average'] == mean
        assert group['distribution']['items'] == [{'value': v, 'count': n} for v, n in frequency]
    data['mode'] = 'MERGED'
    missing = a.call('POST', '/question-statistics/cross-survey', data, status=422)
    assert missing.json()['error']['code'] == 'COMPARISON_REQUIRED'
    data['comparisonSpec'] = {'contextConfirmed': True, 'rationale': '同一测量，同一范围，确认两卷上下文可比'}
    merged = a.call('POST', '/question-statistics/cross-survey', data).json()['data']
    assert len(merged['groups']) == 1 and merged['latestWholeResponseCount'] == 4
    group = merged['groups'][0]
    assert group['answeredParticipationCount'] == 4 and group['distinctAnsweredAccountCount'] == 2
    assert group['average'] == '30'
    assert group['distribution']['items'] == [{'value': v, 'count': 1} for v in ('18', '22', '30', '50')]
    total = sum(Decimal(x['value']) * x['count'] for x in group['distribution']['items'])
    assert total == Decimal('120') and total / group['answeredParticipationCount'] == Decimal(group['average'])
    assert 'sum' not in group  # Test oracle does not change the public API.
    data['comparisonSpec']['bins'] = ['0', '21', '40', '150']
    binned = a.call('POST', '/question-statistics/cross-survey', data).json()['data']['groups'][0]
    assert binned['average'] == '30' and binned['answeredParticipationCount'] == 4
    assert binned['distribution']['boundaries'] == ['0', '21', '40', '150']
    assert binned['distribution']['counts'] == [0, 1, 2, 1, 0]
    c['semantics']['unit'] = '月'
    _, tag = a.get('/question-resources/' + r['resourceId'])
    v3 = a.call('POST', f'/question-resources/{r["resourceId"]}/versions',
        {'baseVersionId': v2['questionVersionId'], 'content': c}, etag=tag, key=uuid.uuid4().hex, status=201).json()['data']
    data['targets'].append({'resourceId': r['resourceId'], 'questionVersionId': v3['questionVersionId']})
    rejected = a.call('POST', '/question-statistics/cross-survey', data, status=422)
    assert rejected.json()['error']['code'] == 'NOT_COMPARABLE'


def test_duplicate_instance_requires_selector_and_empty_group(ctx):
    a = ctx.alice; r, _ = a.resource(); s, st = a.survey()
    path = '/surveys/' + s['surveyId']
    first, st = a.import_to(s['surveyId'], st, r); second, st = a.import_to(s['surveyId'], st, r)
    a.call('POST', path + '/publish', {}, etag=st)
    data = {'targets': [{'resourceId': r['resourceId'], 'questionVersionId': r['questionVersionId']}], 'surveyIds': [s['surveyId']]}
    a.call('POST', '/question-statistics/cross-survey', data, status=422)
    def select(question_id):
        data['instanceSelectors'] = [{'surveyId': s['surveyId'], 'publishedVersion': 1, 'questionId': question_id}]
        report = a.call('POST', '/question-statistics/cross-survey', data).json()['data']
        assert len(report['groups']) == 1
        return report
    empty = select(first['questionId'])['groups'][0]
    assert empty['average'] is None and empty['answeredParticipationCount'] == 0
    submit_answer(a, s['publicCode'], [{'questionId': first['questionId'], 'value': '18'},
                                     {'questionId': second['questionId'], 'value': '30'}])
    del data['instanceSelectors']
    a.call('POST', '/question-statistics/cross-survey', data, status=422)
    for instance, expected in [(first, '18'), (second, '30'), (first, '18')]:
        report = select(instance['questionId']); group = report['groups'][0]
        assert report['latestWholeResponseCount'] == 1
        assert group['answeredParticipationCount'] == 1 and group['distinctAnsweredAccountCount'] == 1
        assert group['average'] == expected
        assert group['distribution']['items'] == [{'value': expected, 'count': 1}]


def test_other_survey_rejected_even_without_responses(ctx):
    r,_=ctx.alice.resource(); other,_=ctx.bob.survey()
    ctx.alice.call('POST','/question-statistics/cross-survey',{'targets':[{'resourceId':r['resourceId'],'questionVersionId':r['questionVersionId']}],
        'surveyIds':[other['surveyId']]},status=404)


def test_latest_version_without_target_does_not_revive_old_answer(ctx):
    a=ctx.alice; r,_=a.resource(); s,st=a.survey(); imported,st=a.import_to(s['surveyId'],st,r)
    p='/surveys/'+s['surveyId']; pub=a.call('POST',p+'/publish',{},etag=st)
    submit_answer(a,s['publicCode'],[{'questionId':imported['questionId'],'value':'18'}])
    s,st=a.get(p)
    deleted=a.call('DELETE',p+'/questions/'+imported['questionId'],etag=st,status=204)
    added=a.call('POST',p+'/questions',{'type':'TEXT','title':'其他题'},etag=deleted.headers['ETag'],status=201)
    q=added.json()['data']['draft']['questions'][0]
    a.call('POST',p+'/publish',{},etag=added.headers['ETag'])
    submit_answer(a,s['publicCode'],[{'questionId':q['questionId'],'value':'新版回答'}])
    result=a.call('POST','/question-statistics/cross-survey',{'targets':[{'resourceId':r['resourceId'],'questionVersionId':r['questionVersionId']}],
        'surveyIds':[s['surveyId']]}).json()['data']
    assert all(g['answeredParticipationCount']==0 for g in result['groups'])
