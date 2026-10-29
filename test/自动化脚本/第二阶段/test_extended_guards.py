"""Route-level auth/CSRF and object-version/atomicity matrices."""
from copy import deepcopy
import hashlib
import uuid

import requests
from bson import BSON, ObjectId

from phase2_support import bank, evidence, expect, raw, scoped_state
from test_sharing import share


def test_all_incremental_route_auth_csrf_and_foreign_objects(ctx):
    a,b=ctx.alice,ctx.bob
    r,rt=a.resource();s,st=a.survey();path='/surveys/'+s['surveyId']
    q=a.call('POST',path+'/questions',{'type':'TEXT','title':'待提取'},etag=st,status=201)
    qid=q.json()['data']['draft']['questions'][0]['questionId']
    bp,bt=bank(a,'权限矩阵')
    params={'rid':r['resourceId'],'vid':r['questionVersionId'],'sid':s['surveyId'],'qid':qid,'bid':bp.rsplit('/',1)[-1]}
    probes=[]
    for rule in ctx.app.url_map.iter_rules():
        if rule.endpoint.split('.')[0] not in ('resources','survey_reuse','sharing','cross_statistics'):continue
        url=rule.rule.removeprefix('/api/v1')
        for key,value in params.items():url=url.replace('<'+key+'>',value)
        for method in sorted(rule.methods-{'OPTIONS','HEAD'}):probes.append((method,url))
    assert len(probes)>=25
    before=scoped_state(ctx,s['surveyId'],[a.uid,b.uid])
    for method,url in probes:
        unauth=requests.request(method,ctx.base+'/api/v1'+url,json={},timeout=40)
        expect(unauth,401,'UNAUTHENTICATED')
        if method not in ('GET','HEAD'):
            expect(raw(a,method,url,headers={'X-CSRF-Token':''}),403,'CSRF_INVALID')
            expect(raw(a,method,url,headers={'Origin':'http://foreign.invalid'}),403,'ORIGIN_DENIED')
        # Collection roots/capabilities intentionally expose the caller's own scope.
        if url in ('/question-resources','/question-banks','/capabilities'):
            if method=='GET':
                data=b.get(url)[0]
                if 'items' in data:assert data['items']==[]
            continue
        if url=='/question-statistics/cross-survey':
            expect(raw(b,method,url,{'targets':[{k:r[k] for k in ('resourceId','questionVersionId')}],'surveyIds':[s['surveyId']]}),404)
            continue
        payload={}
        if url.endswith('/draft'):payload={'content':{'type':'TEXT','title':'越权'}}
        elif '/binding' in url or url.endswith('/question-imports'):payload={k:r[k] for k in ('resourceId','questionVersionId')}
        elif url.endswith('/derivations'):payload={'content':{'type':'TEXT','title':'越权'}}
        elif url.endswith('/restorations'):payload={'baseVersionId':r['questionVersionId'],'restoreFromVersionId':r['questionVersionId']}
        elif '/members/' in url and method=='PUT':payload={'questionVersionId':r['questionVersionId']}
        elif url.endswith('/grants') and method=='PUT':payload={'username':b.name}
        elif '/usage-summary-grants/' in url and method=='PUT':payload={'enabled':False,'policyVersion':'SUMMARY_V1'}
        elif method=='PATCH' and url==bp:payload={'name':'越权'}
        expect(raw(b,method,url,payload,etag=bt if '/question-banks/' in url else q.headers['ETag'] if '/surveys/' in url else rt,
                   key=uuid.uuid4().hex,headers={'If-None-Match':'*'}),404,'NOT_FOUND')
    assert scoped_state(ctx,s['surveyId'],[a.uid,b.uid])==before
    expect(raw(a,'POST','/question-resources',{'unknown':True},key=uuid.uuid4().hex),422,'FIELDS_INVALID')
    evidence('逐路由权限矩阵',{'routes':probes,'checks':'401 / write 403 CSRF+Origin / foreign object 404 / own collection scopes'})
    expect(raw(a,'GET','/question-resources/not-id'),404)


def test_idempotency_payload_actor_and_etag_matrix(ctx):
    a,b=ctx.alice,ctx.bob;key=uuid.uuid4().hex
    data={'content':{'type':'TEXT','title':'同键账号隔离'}}
    first=a.call('POST','/question-resources',data,key=key,status=201)
    replay=a.call('POST','/question-resources',data,key=key)
    assert first.json()['data']['resourceId']==replay.json()['data']['resourceId']
    expect(raw(a,'POST','/question-resources',{'content':{'type':'TEXT','title':'异载荷'}},key=key),409,'IDEMPOTENCY_CONFLICT')
    other=b.call('POST','/question-resources',data,key=key,status=201)
    assert other.json()['data']['resourceId']!=first.json()['data']['resourceId']
    assert ctx.db.operation_keys.count_documents({'keyHash':hashlib.sha256(key.encode()).hexdigest()})==2
    # Resource, bank, and summary are separate ETag namespaces and CAS versions.
    draft,dt=a.resource(fixed=False);rp='/question-resources/'+draft['resourceId']+'/draft'
    bp,bt=bank(a,'ETag题库')
    shared,_=a.resource();share(ctx,shared);s,st=b.survey();b.import_to(s['surveyId'],st,shared,consent=True)
    sp=f'/surveys/{s["surveyId"]}/usage-summary-grants/{shared["resourceId"]}'
    _,gt=b.get(sp)
    objects=[(a,'PATCH',rp,{'content':{'type':'NUMBER','title':'草稿新题','validation':{}}},dt),
             (a,'PATCH',bp,{'name':'改名'},bt),(b,'PUT',sp,{'enabled':False,'policyVersion':'SUMMARY_V1'},gt)]
    for actor,method,path,payload,tag in objects:
        for wrong,code in [('"wrong-0"',409),(tag.rsplit('-',1)[0]+'-999"',409),(None,428)]:
            before=scoped_state(ctx,s['surveyId'],[a.uid,b.uid])
            expect(raw(actor,method,path,payload,etag=wrong),code)
            assert scoped_state(ctx,s['surveyId'],[a.uid,b.uid])==before
        success=actor.call(method,path,payload,etag=tag)
        assert success.headers['ETag']!=tag
        before=scoped_state(ctx,s['surveyId'],[a.uid,b.uid])
        expect(raw(actor,method,path,payload,etag=tag),409,'VERSION_CONFLICT')
        assert scoped_state(ctx,s['surveyId'],[a.uid,b.uid])==before


def test_summary_import_interruption_and_success_receipts(ctx,monkeypatch):
    from pymongo.collection import Collection
    from survey_app import phase2_core
    a,b=ctx.alice,ctx.bob;r,_=a.resource();share(ctx,r)
    for point in ('grant_written','binding_written'):
        s,st=b.survey(point);sid=ObjectId(s['surveyId']);key=uuid.uuid4().hex
        payload={k:r[k] for k in ('resourceId','questionVersionId')}
        payload['usageSummaryConsent']={'accepted':True,'policyVersion':'SUMMARY_V1'}
        before=scoped_state(ctx,s['surveyId'],[a.uid,b.uid])
        with monkeypatch.context() as patch:
            if point=='grant_written':
                original=Collection.update_one
                def interrupted(collection,*args,**kwargs):
                    result=original(collection,*args,**kwargs)
                    if collection.database.name==ctx.db.name and collection.name=='usage_summary_grants':
                        raise RuntimeError('isolated fault after summary grant write')
                    return result
                patch.setattr(Collection,'update_one',interrupted)
            else:
                original=phase2_core.sync_usages
                def interrupted(*args,**kwargs):
                    original(*args,**kwargs)
                    raise RuntimeError('isolated fault after binding and projection writes')
                patch.setattr(phase2_core,'sync_usages',interrupted)
            expect(raw(b,'POST',f'/surveys/{s["surveyId"]}/question-imports',payload,st,key),500)
        assert scoped_state(ctx,s['surveyId'],[a.uid,b.uid])==before
        receipt={'actorId':ObjectId(b.uid),'keyHash':hashlib.sha256(key.encode()).hexdigest(),'useCase':'IMPORT'}
        assert ctx.db.operation_keys.find_one(receipt) is None
        first=b.call('POST',f'/surveys/{s["surveyId"]}/question-imports',payload,etag=st,key=key,status=201)
        again=b.call('POST',f'/surveys/{s["surveyId"]}/question-imports',payload,etag=st,key=key)
        assert first.json()['data']['questionId']==again.json()['data']['questionId']
        assert again.json()['data']['replayed'] is True
        assert ctx.db.operation_keys.count_documents(receipt)==1
        assert ctx.db.question_usages.count_documents({'surveyId':sid})==1
        assert ctx.db.usage_summary_grants.count_documents({'surveyId':sid})==1
