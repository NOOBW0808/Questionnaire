import importlib.util
import os
from pathlib import Path
import uuid

from bson import json_util, BSON
from pymongo import MongoClient
import pytest

from survey_app.schema_tools import install_empty, inspect_stage

FILE = Path(__file__).resolve().parents[3] / 'code/scripts/phase2_schema.py'
spec=importlib.util.spec_from_file_location('phase2_schema',FILE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.fixture
def legacy():
    client=MongoClient(os.environ['MONGODB_URI'],tz_aware=True)
    name='questionnaire_p2_test_migration_'+uuid.uuid4().hex[:16]
    database=client[name]
    try:
        install_empty(database,m.BASE)
        fixtures=json_util.loads((Path(__file__).parent/'fixtures/legacy.ejson').read_text('utf-8'))
        for name,rows in fixtures.items():
            if rows:database[name].insert_many(rows)
        yield database
    finally:
        assert database.name.startswith('questionnaire_p2_test_migration_')
        client.drop_database(database.name);client.close()


def test_old_schema_detected_and_upgrade_idempotent_keeps_history(legacy,tmp_path):
    assert inspect_stage(legacy)==1
    m.preflight(legacy)
    baseline=m.logical_hashes(legacy)
    backup=m.backup(legacy,tmp_path/'backup')
    m.apply(legacy,backup)
    with pytest.raises(RuntimeError,match='尚未激活'):
        inspect_stage(legacy)
    m.apply(legacy,backup)
    assert m.logical_hashes(legacy)==baseline
    assert legacy.question_resources.count_documents({})==0
    assert legacy.question_usages.count_documents({})==0
    m.verify(legacy,backup);m.activate(legacy,backup)
    assert inspect_stage(legacy)==2
    with pytest.raises(RuntimeError,match='已激活'):
        m.rollback(legacy,backup)


def test_partial_ddl_resume_and_preactivation_rollback(legacy,tmp_path):
    backup=m.backup(legacy,tmp_path/'backup')
    cfg=m.TARGET['collections']['question_resources']
    legacy.create_collection('question_resources',validator=cfg['validator'],validationLevel='strict',validationAction='error')
    with pytest.raises(RuntimeError,match='中间态'):
        inspect_stage(legacy)
    m.apply(legacy,backup)
    m.rollback(legacy,backup)
    assert inspect_stage(legacy)==1


def test_backup_restore_copy_verifies_original_hashes(legacy,tmp_path):
    backup=m.backup(legacy,tmp_path/'backup')
    dest='questionnaire_p2_test_restore_'+uuid.uuid4().hex
    try:
        assert m.restore_copy(legacy,backup,dest)['hashesVerified']
        assert m.logical_hashes(legacy)==m.logical_hashes(legacy.client[dest])
        with pytest.raises(RuntimeError,match='新专用数据库'):
            m.restore_copy(legacy,backup,legacy.name)
    finally:legacy.client.drop_database(dest)


def test_unknown_validator_and_orphans_are_blocked_without_deletion(legacy,tmp_path):
    response=legacy.responses.find_one({})
    before=BSON.encode(response)
    legacy.participation_guards.delete_one({'_id':response['participationId']})  # Isolated fault injection only.
    with pytest.raises(RuntimeError,match='关联完整性'):
        m.preflight(legacy)
    assert BSON.encode(legacy.responses.find_one({'_id':response['_id']}))==before


def test_changed_history_blocks_apply(legacy,tmp_path):
    backup=m.backup(legacy,tmp_path/'backup')
    legacy.surveys.update_one({}, {'$set':{'draft.title':'备份后被修改'}})
    with pytest.raises(RuntimeError,match='旧8集合内容变化'):
        m.apply(legacy,backup)


def test_corrupt_backup_is_rejected(legacy,tmp_path):
    backup=m.backup(legacy,tmp_path/'backup')
    with (backup.parent/'surveys.bson').open('ab') as f:f.write(b'corrupt')
    with pytest.raises(RuntimeError,match='哈希无效'):
        m.apply(legacy,backup)


def test_unknown_schema_and_legacy_response_shape_are_blocked(legacy):
    from copy import deepcopy
    cfg=deepcopy(m.BASE['collections']['surveys']['validator'])
    cfg['$jsonSchema']['required'].append('unknownRequiredField')
    legacy.command({'collMod':'surveys','validator':cfg})
    with pytest.raises(RuntimeError,match='漂移'):
        m.preflight(legacy)
    legacy.command({'collMod':'surveys','validator':m.BASE['collections']['surveys']['validator']})
    legacy.responses.update_one({}, {'$set':{'schemaVersion':1}},bypass_document_validation=True)
    with pytest.raises(RuntimeError):
        m.preflight(legacy)
    assert legacy.responses.find_one({'schemaVersion':1}) is not None


def test_failed_ddl_keeps_ledger_and_can_resume(legacy,tmp_path,monkeypatch):
    from pymongo.collection import Collection
    backup=m.backup(legacy,tmp_path/'backup');original=Collection.create_index
    def broken(collection,*args,**kwargs):
        if collection.database.name==legacy.name and collection.name=='question_versions':
            raise RuntimeError('injected DDL interruption')
        return original(collection,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Collection,'create_index',broken)
        with pytest.raises(RuntimeError,match='interruption'):
            m.apply(legacy,backup)
    assert legacy.migration_runs.find_one({'planId':m.PLAN})['state']=='FAILED'
    m.apply(legacy,backup);m.verify(legacy,backup)
    result=m.rollback(legacy,backup)
    archive=json_util.loads(Path(result['evidence']).read_text('utf-8'))
    assert archive['state']=='ROLLED_BACK' and archive['ledger']['migration_runs']
    assert inspect_stage(legacy)==1


def test_complete_old_schema_runs_original_features_without_auto_upgrade(legacy,monkeypatch):
    from survey_app import create_app
    from survey_app.security import _rates,_lock
    with _lock:_rates.clear()
    monkeypatch.setenv('MONGODB_DATABASE',legacy.name)
    app=create_app();app.config['APP_ORIGIN']='http://localhost'
    try:
        client=app.test_client();credentials={'username':'compat_'+uuid.uuid4().hex[:12],'password':'TestPass!2026'}
        assert client.post('/api/v1/auth/register',json=credentials).status_code==201
        login=client.post('/api/v1/auth/login',json=credentials)
        h={'Origin':'http://localhost','X-CSRF-Token':login.json['data']['csrfToken']}
        assert client.get('/api/v1/capabilities').json['data']['schemaStage']==1
        assert client.post('/api/v1/surveys',json={'title':'兼容模式正常创建'},headers=h).status_code==201
        new=client.post('/api/v1/question-resources',json={'content':{}},headers=h)
        assert new.status_code==409 and new.json['error']['code']=='SCHEMA_UPGRADE_REQUIRED'
        assert set(legacy.list_collection_names())==set(m.BASE_NAMES)
    finally:app.extensions['database'].client.close()


def test_same_index_name_with_different_meaning_stops_without_overwrite(legacy,tmp_path):
    backup=m.backup(legacy,tmp_path/'backup')
    cfg=m.TARGET['collections']['question_resources']
    legacy.create_collection('question_resources',validator=cfg['validator'],validationLevel='strict',validationAction='error')
    index=cfg['indexes'][0]
    legacy.question_resources.create_index([('wrongField',1)],name=index['name'])
    with pytest.raises(RuntimeError,match='同名异义'):
        m.apply(legacy,backup)
    assert dict(next(x for x in legacy.question_resources.list_indexes() if x['name']==index['name'])['key'])=={'wrongField':1}
