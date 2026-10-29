import uuid

from bson import ObjectId
from pymongo.errors import AutoReconnect

from conftest import submit_answer
from test_cross_statistics import scenario


def test_late_cursor_interruption_never_exposes_earlier_metrics(ctx,monkeypatch):
    from survey_app import cross_statistics as module
    _,_,_,data=scenario(ctx)
    original=module.latest_envelopes;seen=[]
    def broken(sids,session):
        with original(sids,session) as cursor:
            for row in cursor:
                seen.append(row['_id']);yield row
                raise AutoReconnect('isolated cursor interruption after one valid row')
    monkeypatch.setattr(module,'latest_envelopes',broken)
    r=ctx.alice.call('POST','/question-statistics/cross-survey',data,status=503).json()
    assert len(seen)==1 and 'data' not in r and 'groups' not in str(r)


def test_latest_actual_jump_does_not_backfill_old_target_answer(ctx):
    a=ctx.alice;r,_=a.resource();s,tag=a.survey();path='/surveys/'+s['surveyId']
    added=a.call('POST',path+'/questions',{'type':'SINGLE','title':'是否继续','options':[{'label':'继续'},{'label':'结束'}]},etag=tag,status=201)
    gate=added.json()['data']['draft']['questions'][0]
    imp,tag=a.import_to(s['surveyId'],added.headers['ETag'],r)
    updated=a.call('PUT',path+'/questions/'+gate['questionId']+'/jump-rules',{'rules':[{'priority':1,'operator':'EQ','value':gate['options'][1]['optionId'],'target':'END'}]},etag=tag)
    a.call('POST',path+'/publish',{},etag=updated.headers['ETag'])
    submit_answer(a,s['publicCode'],[{'questionId':gate['questionId'],'value':gate['options'][0]['optionId']},{'questionId':imp['questionId'],'value':'18'}])
    submit_answer(a,s['publicCode'],[{'questionId':gate['questionId'],'value':gate['options'][1]['optionId']}])
    result=a.call('POST','/question-statistics/cross-survey',{'targets':[{'resourceId':r['resourceId'],'questionVersionId':r['questionVersionId']}],
        'surveyIds':[s['surveyId']]}).json()['data']
    assert result['exclusions']['SKIPPED']==1 and result['groups'][0]['average'] is None


def test_choice_merge_requires_complete_semantic_mapping(ctx):
    a=ctx.alice;targets=[];sids=[];mappings=[]
    for i in range(2):
        r,_=a.resource(kind='SINGLE',title='是否参加'+str(i));s,tag=a.survey();imp,tag=a.import_to(s['surveyId'],tag,r)
        pub=a.call('POST','/surveys/'+s['surveyId']+'/publish',{},etag=tag).json()['data']
        q=pub['draft']['questions'][0];submit_answer(a,s['publicCode'],[{'questionId':imp['questionId'],'value':q['options'][0]['optionId']}])
        v=a.get(f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}')[0]
        targets.append({'resourceId':r['resourceId'],'questionVersionId':r['questionVersionId']});sids.append(s['surveyId'])
        mappings.append({'questionVersionId':r['questionVersionId'],'mapping':[{'semanticOptionId':o['semanticOptionId'],'categoryId':str(n)} for n,o in enumerate(v['content']['options'])]})
    data={'targets':targets,'surveyIds':sids,'mode':'MERGED','comparisonSpec':{'rationale':'同一含义选项逐一核实','contextConfirmed':True,
        'categories':[{'categoryId':'0','label':'是'},{'categoryId':'1','label':'否'}],'optionMappings':mappings}}
    result=a.call('POST','/question-statistics/cross-survey',data).json()['data']['groups'][0]
    assert result['answeredParticipationCount']==2 and result['distinctAnsweredAccountCount']==1
    assert result['options'][0]['count']==2 and result['options'][1]['count']==0
    data['comparisonSpec']['optionMappings'][0]['mapping'].pop()
    a.call('POST','/question-statistics/cross-survey',data,status=422)


def test_decimal_average_and_text_count(ctx):
    a=ctx.alice;r,tag=a.resource();path='/question-resources/'+r['resourceId']
    c=a.get(path+'/versions/'+r['questionVersionId'])[0]['content'];c['validation']={'integerOnly':False}
    v=a.call('POST',path+'/versions',{'baseVersionId':r['questionVersionId'],'content':c},etag=tag,key=uuid.uuid4().hex,status=201).json()['data']
    s,tag=a.survey();imp,tag=a.import_to(s['surveyId'],tag,v);a.call('POST','/surveys/'+s['surveyId']+'/publish',{},etag=tag)
    submit_answer(a,s['publicCode'],[{'questionId':imp['questionId'],'value':'0.1'}]);submit_answer(ctx.bob,s['publicCode'],[{'questionId':imp['questionId'],'value':'0.2'}])
    result=a.call('POST','/question-statistics/cross-survey',{'targets':[{'resourceId':r['resourceId'],'questionVersionId':v['questionVersionId']}],'surveyIds':[s['surveyId']]}).json()['data']
    assert result['groups'][0]['average']=='0.15'
    text,_=a.resource(kind='TEXT');t,tag=a.survey();imp,tag=a.import_to(t['surveyId'],tag,text);a.call('POST','/surveys/'+t['surveyId']+'/publish',{},etag=tag)
    submit_answer(a,t['publicCode'],[{'questionId':imp['questionId'],'value':'不应公开的原始文本'}])
    out=a.call('POST','/question-statistics/cross-survey',{'targets':[{'resourceId':text['resourceId'],'questionVersionId':text['questionVersionId']}],'surveyIds':[t['surveyId']]}).json()['data']
    assert out['groups'][0]['answeredParticipationCount']==1 and '不应公开的原始文本' not in str(out)


def test_duplicate_snapshot_lookup_is_an_error_not_double_count(ctx):
    _,_,surveys,data=scenario(ctx)
    collection=ctx.db.survey_versions
    index=next(x for x in collection.list_indexes() if x.get('unique') and list(x['key'])==['surveyId','publishedVersion'])
    row=collection.find_one({'surveyId':ObjectId(surveys[1][0]['surveyId'])});row['_id']=ObjectId()
    # This fixture is the random disposable database, never a business DB.
    collection.drop_index(index['name'])
    try:
        collection.insert_one(row)
        result=ctx.alice.call('POST','/question-statistics/cross-survey',data,status=500).json()
        assert result['error']['code']=='DATA_INTEGRITY_ERROR' and 'data' not in result
    finally:
        collection.delete_one({'_id':row['_id']})
        collection.create_index(list(index['key'].items()),name=index['name'],unique=True)
