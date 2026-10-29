"""Five planned statistical/integrity cases, backed by real successful responses."""
from copy import deepcopy
import uuid

import allure
from bson import BSON, ObjectId

from conftest import submit_answer
from phase2_support import bound, evidence, expect, raw, target
from test_sharing import share


def test_multiple_complete_mapping_and_response_denominator(ctx):
    a = ctx.alice
    versions, surveys, maps = [], [], []
    for labels in [('红','蓝'),('红色','蓝色')]:
        data = {'content': {'type':'MULTIPLE','title':'喜欢的颜色','options':[{'label':x} for x in labels],
            'validation': {'exactSelections':2}, 'semantics':{'measure':'颜色偏好','unit':'类别','timeBasis':'当前','population':'本人'}}}
        r = a.call('POST','/question-resources',data,key=uuid.uuid4().hex,status=201).json()['data']
        s, imp, _ = bound(a, r); surveys.append(s); versions.append(r)
        q = a.get('/surveys/' + s['surveyId'])[0]['draft']['questions'][0]
        submit_answer(a,s['publicCode'],[{'questionId': imp['questionId'], 'value':[x['optionId'] for x in q['options']]}])
        c = a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]['content']
        maps.append({'questionVersionId':r['questionVersionId'],'mapping':[
            {'semanticOptionId':o['semanticOptionId'],'categoryId':k} for o,k in zip(c['options'],('red','blue'))]})
    spec = {'contextConfirmed':True,'rationale':'两卷均问本人当前颜色偏好，同一类别单位',
        'categories':[{'categoryId':'red','label':'红'},{'categoryId':'blue','label':'蓝'}], 'optionMappings':maps}
    data = {'targets':[{k:r[k] for k in ('resourceId','questionVersionId')} for r in versions],
            'surveyIds':[s['surveyId'] for s in surveys], 'mode':'MERGED','comparisonSpec':spec}
    result = a.call('POST','/question-statistics/cross-survey',data).json()['data']['groups'][0]
    assert result['answeredParticipationCount'] == 2 and result['distinctAnsweredAccountCount'] == 1
    assert {x['categoryId']:x['count'] for x in result['options']} == {'red':2,'blue':2}
    for mutate in ('missing','duplicate','unknown'):
        invalid = deepcopy(data); mapping = invalid['comparisonSpec']['optionMappings'][0]['mapping']
        if mutate == 'missing': mapping.pop()
        if mutate == 'duplicate': mapping.append(deepcopy(mapping[0]))
        if mutate == 'unknown': mapping[0]['categoryId'] = 'unknown'
        expect(raw(a,'POST','/question-statistics/cross-survey',invalid),422,'MAPPING_INVALID')


def test_same_hash_context_needs_explicit_comparison(ctx):
    a = ctx.alice; r, _ = a.resource()
    surveys = []
    for description, value in [('本卷回答者本人年龄','18'),('本卷调查家庭成员年龄','30')]:
        s, tag = a.survey()
        edit = a.call('PATCH','/surveys/'+s['surveyId'],{'description':description},etag=tag)
        imp, tag = a.import_to(s['surveyId'],edit.headers['ETag'],r)
        a.call('POST','/surveys/'+s['surveyId']+'/publish',{},etag=tag)
        submit_answer(a,s['publicCode'],[{'questionId':imp['questionId'],'value':value}]);surveys.append(s)
    data = target(r,surveys)
    grouped = a.call('POST','/question-statistics/cross-survey',data).json()['data']
    assert len(grouped['groups']) == 2
    assert sorted(x['average'] for x in grouped['groups']) == ['18','30']
    for g in grouped['groups']:
        assert g['questionVersionIds'] == [r['questionVersionId']] and g['answeredParticipationCount'] == 1
    expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'mode':'MERGED'}),422,'COMPARISON_REQUIRED')
    conflicting = {'contextConfirmed':True,'rationale':'错误地将家庭成员声明改为本人',
        'semanticDeclarations':[{'questionVersionId':r['questionVersionId'],'measure':'其他测量','unit':'岁','timeBasis':'填写当日','population':'受访者'}]}
    expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'mode':'MERGED','comparisonSpec':conflicting}),422,'NOT_COMPARABLE')


def test_invalid_bins_scope_and_zero_sample_boundaries(ctx):
    a = ctx.alice; r, _ = a.resource(); s, _, _ = bound(a,r)
    data = target(r,[s])
    for bins in (['0','0'],['10','0'],['NaN','10'],['0'],['0','Infinity']):
        out = expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'comparisonSpec':{'bins':bins}}),422)
        evidence('非法分箱拒绝',{'input':bins,'actual':out.json()})
    foreign,_=ctx.bob.survey()
    expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'surveyIds':[foreign['surveyId']]}),404)
    expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'surveyIds':[s['surveyId'],s['surveyId']]}),422,'SCOPE_INVALID')
    invalid_id = raw(a,'POST','/question-statistics/cross-survey',{**data,'surveyIds':['invalid']})
    expect(raw(a,'POST','/question-statistics/cross-survey',{**data,'targets':data['targets']*2}),422)
    zero=a.call('POST','/question-statistics/cross-survey',data).json()['data']['groups'][0]
    assert zero['average'] is None and zero['answeredParticipationCount']==0 and zero['distribution']['items']==[]
    expect(invalid_id,404)


def test_whole_path_answer_and_account_anomalies_fail_entire_report(ctx):
    a=ctx.alice;r,_=a.resource();surveys=[]
    for value in ('18','30'):
        s,imp,_=bound(a,r,anonymous=False)
        result=submit_answer(a,s['publicCode'],[{'questionId':imp['questionId'],'value':value}])
        surveys.append((s,result))
    data=target(r,[s for s,_ in surveys])
    assert a.call('POST','/question-statistics/cross-survey',data).json()['data']['latestWholeResponseCount']==2
    damaged=ctx.db.responses.find_one({'_id':ObjectId(surveys[1][1]['responseId'])})
    guard=ctx.db.participation_guards.find_one({'_id':damaged['participationId']})
    for damage in ('missing_answer','duplicate_answer','wrong_path','wrong_respondent','nonexistent_account','wrong_guard_survey'):
        rdoc=deepcopy(damaged);gdoc=deepcopy(guard)
        if damage=='missing_answer':rdoc['answers']=[]
        if damage=='duplicate_answer':rdoc['answers']*=2
        if damage=='wrong_path':rdoc['visitedQuestionIds']=[]
        if damage=='wrong_respondent':rdoc['respondentUserId']=ObjectId(ctx.bob.uid)
        if damage=='nonexistent_account':gdoc['userId']=ObjectId()
        if damage=='wrong_guard_survey':gdoc['surveyId']=ObjectId()
        try:
            ctx.db.responses.replace_one({'_id':rdoc['_id']},rdoc,bypass_document_validation=True)
            ctx.db.participation_guards.replace_one({'_id':gdoc['_id']},gdoc,bypass_document_validation=True)
            # Force a valid candidate before the corrupted one; no partial result may escape.
            from survey_app import cross_statistics
            original=cross_statistics.latest_envelopes
            def ordered(sids,session):
                return iter(sorted(original(sids,session),key=lambda row: row['_id']==damaged['_id']))
            from unittest.mock import patch
            with patch.object(cross_statistics,'latest_envelopes',ordered):
                out=expect(raw(a,'POST','/question-statistics/cross-survey',data),500,'DATA_INTEGRITY_ERROR')
            assert set(out.json()['error'])=={'code','message'}
            evidence('正常候选之后整份失败：'+damage,out.json())
        finally:
            ctx.db.responses.replace_one({'_id':damaged['_id']},damaged)
            ctx.db.participation_guards.replace_one({'_id':guard['_id']},guard)
    assert a.call('POST','/question-statistics/cross-survey',data).json()['data']['groups'][0]['answeredParticipationCount']==2


def test_recursive_privacy_execution_and_summary_allowlists(ctx):
    a,b=ctx.alice,ctx.bob
    r,_=a.resource(kind='TEXT');share(ctx,r)
    sid,tag=b.survey('私有问卷上下文');imp,tag=b.import_to(sid['surveyId'],tag,r,consent=True)
    b.call('POST','/surveys/'+sid['surveyId']+'/publish',{},etag=tag)
    definition=b.call('GET','/public-surveys/'+sid['publicCode']).json()
    forbidden_definition={'reuseBinding','resourceId','questionVersionId','ownerId','createdBy','adoptionSource','derivedFrom','grantorId','recipientId'}
    def keys(value):
        if isinstance(value,dict):
            return set(value)|set().union(*(keys(v) for v in value.values()),set())
        if isinstance(value,list):return set().union(*(keys(v) for v in value),set())
        return set()
    assert not keys(definition)&forbidden_definition
    out=b.call('POST','/public-surveys/'+sid['publicCode']+'/attempts',{},status=201)
    assert not keys(out.json())&forbidden_definition
    secret='私密文本_'+uuid.uuid4().hex
    submit_answer(b,sid['publicCode'],[{'questionId':imp['questionId'],'value':secret}])
    report=b.call('POST','/question-statistics/cross-survey',target(r,[sid])).json()
    forbidden={'userId','ownerId','respondentUserId','participationId','responseId','attemptId','answers','username','email','phone','adoptionSource','grantorId','recipientId'}
    assert not keys(report)&forbidden
    assert secret not in str(report) and a.uid not in str(report) and b.uid not in str(report)
    assert report['data']['groups'][0]['answeredParticipationCount']==1
    summary=a.get('/question-resources/'+r['resourceId']+'/usages')[0]
    allowed={'surveyId','surveyTitle','ownership','resourceId','questionVersionId','publishedVersion','usageStage','surveyStatus','instanceCount'}
    assert all(set(x)==allowed for x in summary['items'])
    assert set(summary)=={'items','coverage','totalVisible','nextCursor'}
    assert not keys(summary)&forbidden and secret not in str(summary)
    # Include ADOPT-origin resource in public filling and successful/failed reports.
    s,tag=a.survey();q=a.call('POST','/surveys/'+s['surveyId']+'/questions',{'type':'NUMBER','title':'年龄'},etag=tag,status=201)
    qid=q.json()['data']['draft']['questions'][0]['questionId']
    adopted=a.call('POST',f'/surveys/{s["surveyId"]}/questions/{qid}/adoption',{},etag=q.headers['ETag'],key=uuid.uuid4().hex,status=201)
    a.call('POST','/surveys/'+s['surveyId']+'/publish',{},etag=adopted.headers['ETag'])
    assert not keys(a.call('GET','/public-surveys/'+s['publicCode']).json())&forbidden_definition
    submit_answer(a,s['publicCode'],[{'questionId':qid,'value':'20'}])
    resource=adopted.json()['data'];request=target(resource,[s])
    successful=a.call('POST','/question-statistics/cross-survey',request).json()
    assert not keys(successful)&forbidden
    request['mode']='MERGED'
    error=expect(raw(a,'POST','/question-statistics/cross-survey',request),422).json()
    assert not keys(error)&forbidden and secret not in str(error)
