"""Concurrent HTTP operations and injected interruption against randomized test DBs."""
from concurrent.futures import ThreadPoolExecutor
import uuid

from bson import ObjectId, BSON
from pymongo.client_session import ClientSession
from pymongo.errors import AutoReconnect

from test_sharing import share


def raw(client, method, path, data, tag=None, key=None):
    h={'Origin':client.base,'X-CSRF-Token':client.csrf}
    if tag:h['If-Match']=tag
    if key:h['Idempotency-Key']=key
    return client.http.request(method,client.base+'/api/v1'+path,json=data,headers=h,timeout=40)


def test_concurrent_fix_has_one_success_and_one_stale_etag(ctx):
    a=ctx.alice;r,tag=a.resource();path='/question-resources/'+r['resourceId']
    c=a.get(path+'/versions/'+r['questionVersionId'])[0]['content'];c['title']='并发新版本'
    def fix(_):return raw(a,'POST',path+'/versions',{'baseVersionId':r['questionVersionId'],'content':c},tag,uuid.uuid4().hex)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(fix,range(2)))
    assert sorted(x.status_code for x in responses)==[201,409]
    assert ctx.db.question_versions.count_documents({'resourceId':ObjectId(r['resourceId'])})==2


def test_concurrent_same_key_creates_one_resource_and_replays(ctx):
    a=ctx.alice;key=uuid.uuid4().hex;data={'content':{'type':'TEXT','title':'并发幂等','validation':{}}}
    def create(_):return raw(a,'POST','/question-resources',data,key=key)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(create,range(2)))
    assert sorted(x.status_code for x in responses)==[200,201]
    assert len({x.json()['data']['resourceId'] for x in responses})==1
    a.call('POST','/question-resources',{'content':{'type':'TEXT','title':'换内容'}},key=key,status=409)


def test_adopt_interruption_rolls_back_every_composed_write(ctx,monkeypatch):
    from survey_app import phase2_core
    a=ctx.alice;s,tag=a.survey();path='/surveys/'+s['surveyId']
    r=a.call('POST',path+'/questions',{'type':'TEXT','title':'旧题'},etag=tag,status=201)
    q=r.json()['data']['draft']['questions'][0];tag=r.headers['ETag'];before=BSON.encode(ctx.db.surveys.find_one({'_id':ObjectId(s['surveyId'])}))
    counts={n:ctx.db[n].count_documents({}) for n in ('question_resources','question_versions','operation_keys','question_usages')}
    def interrupted(*args,**kwargs):raise RuntimeError('test interruption before projection')
    monkeypatch.setattr(phase2_core,'sync_usages',interrupted)
    a.call('POST',path+'/questions/'+q['questionId']+'/adoption',{},etag=tag,key=uuid.uuid4().hex,status=500)
    assert BSON.encode(ctx.db.surveys.find_one({'_id':ObjectId(s['surveyId'])}))==before
    assert counts=={n:ctx.db[n].count_documents({}) for n in counts}


def test_summary_close_import_race_never_silently_reopens_grant(ctx):
    r,_=ctx.alice.resource();share(ctx,r);b=ctx.bob;s,tag=b.survey()
    _,tag=b.import_to(s['surveyId'],tag,r,consent=True)
    path=f'/surveys/{s["surveyId"]}/usage-summary-grants/{r["resourceId"]}'
    _,grant_tag=b.get(path)
    def close():return raw(b,'PUT',path,{'enabled':False,'policyVersion':'SUMMARY_V1'},grant_tag)
    # Exclude API result metadata from the import body.
    def imp():return raw(b,'POST',f'/surveys/{s["surveyId"]}/question-imports',
        {'resourceId':r['resourceId'],'questionVersionId':r['questionVersionId']},tag,uuid.uuid4().hex)
    with ThreadPoolExecutor(2) as pool:
        c=pool.submit(close);i=pool.submit(imp);closed,imported=c.result(),i.result()
    assert closed.status_code==200
    assert imported.status_code in (201,409)
    assert b.get(path)[0]['enabled'] is False
    assert ctx.alice.get('/question-resources/'+r['resourceId']+'/usages')[0]['totalVisible']==0


def test_unknown_commit_result_retries_same_commit_not_business_callback(ctx,monkeypatch):
    original=ClientSession.commit_transaction;calls=[]
    def uncertain(session):
        result=original(session);calls.append(session.session_id)
        if len(calls)==1:
            error=AutoReconnect('isolated injected unknown outcome after successful commit')
            error._add_error_label('UnknownTransactionCommitResult')
            raise error
        return result
    monkeypatch.setattr(ClientSession,'commit_transaction',uncertain)
    r,_=ctx.alice.resource('提交待确认')
    assert len(calls)==2 and calls[0]==calls[1]
    assert ctx.db.question_versions.count_documents({'resourceId':ObjectId(r['resourceId'])})==1


def test_bank_creation_idempotent_and_no_orphan_receipt(ctx):
    a=ctx.alice;key=uuid.uuid4().hex
    first=a.call('POST','/question-banks',{'name':'幂等题库'},key=key,status=201)
    again=a.call('POST','/question-banks',{'name':'幂等题库'},key=key)
    assert first.json()['data']['bankId']==again.json()['data']['bankId'] and again.json()['data']['replayed']
