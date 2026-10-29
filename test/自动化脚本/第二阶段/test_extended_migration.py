"""Lifecycle and real-binding projection migration in separate disposable DBs."""
from contextlib import contextmanager
from copy import deepcopy
import os
import threading
import uuid

from bson import BSON, ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection
from werkzeug.serving import make_server

from conftest import Client, Quiet, submit_answer
from phase2_support import evidence, version
from test_migration import legacy, m
from survey_app.schema_tools import install_empty


@contextmanager
def http_database(database, monkeypatch):
    """A real HTTP app bound explicitly to this test's database; stop for DDL."""
    from survey_app import create_app
    from survey_app.security import _rates, _lock
    with monkeypatch.context() as patch:
        patch.setenv('MONGODB_DATABASE',database.name)
        patch.setenv('COOKIE_SECURE','false')
        with _lock:_rates.clear()
        app=create_app()
        server=make_server('127.0.0.1',0,app,threaded=True,request_handler=Quiet)
        base=f'http://127.0.0.1:{server.server_port}';app.config['APP_ORIGIN']=base
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield app,base
        finally:
            server.shutdown();server.server_close();thread.join(10)
            app.extensions['database'].client.close()


def test_legacy_lifecycle_across_upgrade_keeps_pending_attempt(legacy,tmp_path,monkeypatch):
    database=legacy
    with http_database(database,monkeypatch) as (app,base):
        actor=Client(base)
        assert app.extensions['database'].schema_stage==1
        s,tag=actor.survey('跨升级的旧问卷');path='/surveys/'+s['surveyId']
        added=actor.call('POST',path+'/questions',{'type':'NUMBER','title':'原年龄','validation':{'integerOnly':True,'minValue':'0','maxValue':'150'}},etag=tag,status=201)
        qid=added.json()['data']['draft']['questions'][0]['questionId']
        actor.call('POST',path+'/publish',{},etag=added.headers['ETag'])
        successful=submit_answer(actor,s['publicCode'],[{'questionId':qid,'value':'20'}])
        pending=actor.call('POST','/public-surveys/'+s['publicCode']+'/attempts',{},status=201)
    sid=ObjectId(s['surveyId']);response_id=ObjectId(successful['responseId'])
    snapshot=BSON.encode(database.survey_versions.find_one({'surveyId':sid,'publishedVersion':1}))
    saved=BSON.encode(database.responses.find_one({'_id':response_id}))
    pending_before=BSON.encode(database.attempts.find_one({'_id':ObjectId(pending.json()['data']['attemptId'])}))
    backup=m.backup(database,tmp_path/'legacy-backup');hashes=m.logical_hashes(database)
    m.apply(database,backup);m.verify(database,backup);m.activate(database,backup)
    assert m.logical_hashes(database)==hashes
    assert BSON.encode(database.attempts.find_one({'_id':ObjectId(pending.json()['data']['attemptId'])}))==pending_before
    with http_database(database,monkeypatch) as (app,base):
        actor.base=base
        assert app.extensions['database'].schema_stage==2
        _,tag=actor.get(path)
        adopt=actor.call('POST',path+'/questions/'+qid+'/adoption',{},etag=tag,key=uuid.uuid4().hex,status=201)
        r=adopt.json()['data'];_,rt=actor.get('/question-resources/'+r['resourceId'])
        v2,rt=version(actor,r,rt,lambda c:c['validation'].update(minValue='40'))
        restored=actor.call('POST','/question-resources/'+r['resourceId']+'/restorations',
            {'baseVersionId':v2['questionVersionId'],'restoreFromVersionId':r['questionVersionId']},etag=rt,key=uuid.uuid4().hex,status=201)
        assert database.question_versions.find_one({'_id':ObjectId(restored.json()['data']['questionVersionId'])})['originKind']=='RESTORE'
        _,tag=actor.get(path)
        rebound=actor.call('PUT',path+'/questions/'+qid+'/binding',{k:v2[k] for k in ('resourceId','questionVersionId')},etag=tag)
        actor.call('POST',path+'/publish',{},etag=rebound.headers['ETag'])
        # Only after rebind+republish, finish the attempt created before the upgrade.
        aid=pending.json()['data']['attemptId']
        answer=actor.call('PUT','/attempts/'+aid+'/answers',{'answers':[{'questionId':qid,'value':'30'}]},etag=pending.headers['ETag'])
        old=actor.call('POST','/attempts/'+aid+'/submit',{},etag=answer.headers['ETag'],key=uuid.uuid4().hex,status=201).json()['data']
        assert database.responses.find_one({'_id':ObjectId(old['responseId'])})['publishedVersion']==1
        fresh=actor.call('POST','/public-surveys/'+s['publicCode']+'/attempts',{},status=201)
        actor.call('PUT','/attempts/'+fresh.json()['data']['attemptId']+'/answers',{'answers':[{'questionId':qid,'value':'30'}]},etag=fresh.headers['ETag'],status=422)
        new=submit_answer(actor,s['publicCode'],[{'questionId':qid,'value':'50'}])
        assert database.responses.find_one({'_id':ObjectId(new['responseId'])})['publishedVersion']==2
        # The existing single-survey statistics endpoint still serves this survey.
        stats=actor.get(path+'/statistics')[0]
        evidence('旧功能统计仍可读',stats)
    assert BSON.encode(database.survey_versions.find_one({'surveyId':sid,'publishedVersion':1}))==snapshot
    assert BSON.encode(database.responses.find_one({'_id':response_id}))==saved


def test_real_binding_projection_rebuild_resumes_without_duplicates(tmp_path,monkeypatch):
    client=MongoClient(os.environ['MONGODB_URI'],tz_aware=True)
    name='questionnaire_p2_test_rebuild_'+uuid.uuid4().hex
    database=client[name]
    try:
        install_empty(database,m.TARGET)
        with http_database(database,monkeypatch) as (app,base):
            a=Client(base);r,_=a.resource();s,tag=a.survey();imp,tag=a.import_to(s['surveyId'],tag,r)
            path='/surveys/'+s['surveyId'];a.call('POST',path+'/publish',{},etag=tag)
            # An unbound question with the same title must not acquire provenance.
            old,tag=a.survey('同名旧题')
            a.call('POST','/surveys/'+old['surveyId']+'/questions',{'type':'NUMBER','title':'年龄'},etag=tag,status=201)
        sid=ObjectId(s['surveyId']);database.question_usages.delete_many({'surveyId':sid})
        before=m.logical_hashes(database);backup=m.backup(database,tmp_path/'projection-backup')
        original=Collection.update_one
        def stop(collection,filter,update,*args,**kwargs):
            result=original(collection,filter,update,*args,**kwargs)
            if collection.database.name==name and collection.name=='migration_items' and filter.get('sourceCollection')=='survey_versions':
                raise RuntimeError('isolated projection checkpoint fault')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(Collection,'update_one',stop)
            import pytest
            with pytest.raises(RuntimeError,match='checkpoint fault'):m.apply(database,backup)
        assert database.migration_runs.find_one({'planId':m.PLAN})['state']=='FAILED'
        m.apply(database,backup);m.verify(database,backup)
        rows=list(database.question_usages.find({'surveyId':sid}).sort('snapshotVersion',1))
        first=[BSON.encode(x) for x in rows]
        assert [(x['snapshotVersion'],x['questionId']) for x in rows]==[(0,imp['questionId']),(1,imp['questionId'])]
        assert all(x['resourceId']==ObjectId(r['resourceId']) and x['questionVersionId']==ObjectId(r['questionVersionId']) for x in rows)
        m.apply(database,backup);m.verify(database,backup)
        assert [BSON.encode(x) for x in database.question_usages.find({'surveyId':sid}).sort('snapshotVersion',1)]==first
        assert m.logical_hashes(database)==before
        assert database.question_usages.count_documents({'surveyId':ObjectId(old['surveyId'])})==0
        keys=[(x['planId'],x['sourceCollection'],x['sourceId'],x['stage']) for x in database.migration_items.find({})]
        assert len(keys)==len(set(keys)) and len(keys)==3
        evidence('重建投影及防重台账',{'usages':rows,'checkpoint':database.migration_runs.find_one({'planId':m.PLAN})['checkpoint']})
    finally:
        assert database.name==name and name.startswith('questionnaire_p2_test_rebuild_')
        client.drop_database(name);client.close()
