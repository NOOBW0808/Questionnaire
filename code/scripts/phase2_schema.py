"""Explicit, restartable phase-two DDL upgrade. Never upgrades on application startup.

All mutation commands require --maintenance and a local BSON backup manifest.
The operator must stop every application writer until activate/rollback completes.
No history deletion, resource inference or automatic backup restoration is provided.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

from bson import BSON, ObjectId, decode_file_iter, json_util
from datetime import datetime, timezone
from pymongo import MongoClient
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from survey_app.schema_tools import specifications, same_options, check_indexes
from survey_app.phase2_core import canonical_hash, check_binding
from survey_app.common import Problem

PLAN = 'P2_ADD_REFERENCES_1'
BASE, TARGET = specifications()
BASE_NAMES = list(BASE['collections'])
NEW_NAMES = [x for x in TARGET['collections'] if x not in BASE_NAMES]
OPERATIONAL = {'migration_runs', 'migration_items'}
PLAN_HASH = canonical_hash({'plan': PLAN, 'schema': TARGET, 'implementation': 1})


def save(path, data):
    Path(path).write_text(json_util.dumps(data, json_options=json_util.CANONICAL_JSON_OPTIONS,
                                         ensure_ascii=False, indent=2), encoding='utf-8')


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def preflight(database, partial=False):
    if database.name in ('admin', 'config', 'local', 'test'):
        raise RuntimeError('必须选择专用应用数据库。')
    hello = database.client.admin.command('hello')
    if not hello.get('isWritablePrimary') or not hello.get('setName') or database.client.server_info()['versionArray'][0] < 7:
        raise RuntimeError('需要MongoDB>=7的可写副本集primary。')
    infos = {x['name']: x for x in database.list_collections()}
    if set(BASE_NAMES) - set(infos):
        raise RuntimeError('缺少旧集合；本工具不把残缺库当作新库。')
    if set(infos) - set(TARGET['collections']):
        raise RuntimeError('含未知集合；请先审查，工具不会删除。')
    for name, info in infos.items():
        candidates = [TARGET['collections'][name]]
        if name in BASE_NAMES:
            candidates.append(BASE['collections'][name])
        if not any(same_options(info, x) for x in candidates):
            raise RuntimeError(f'{name} validator漂移。')
        actual = {i['name']: i for i in database[name].list_indexes()}
        if set(actual)-{'_id_',*(x['name'] for x in TARGET['collections'][name]['indexes'])}:
            raise RuntimeError(f'{name}存在未知索引；保留结构，需显式审查。')
        for index in TARGET['collections'][name]['indexes']:
            found = actual.get(index['name'])
            if found and (list(found['key'].items()) != list(index['key'].items()) or any(
                    found.get(k, False if k in ('unique', 'sparse') else None) != v for k, v in index.items() if k not in ('key', 'name'))):
                raise RuntimeError(f'{name}/{index["name"]}索引同名异义。')
        if not partial:
            check_indexes(database, name, TARGET['collections'][name])
    # Match BSON shapes against the CURRENT validator without changing documents.
    for name in infos:
        validator = infos[name]['options'].get('validator')
        if validator and database[name].find_one({'$nor': [validator]}, {'_id': 1}):
            raise RuntimeError(f'{name}存在不符合当前validator的旧数据；须另审修复。')
    def exists(name, query):
        return database[name].find_one(query, {'_id': 1}) is not None
    def require(ok, code):
        if not ok:
            raise RuntimeError('关联完整性失败：' + code)
    for s in database.sessions.find({}):
        require(exists('users', {'_id':s['userId']}), 'session.user')
    for s in database.surveys.find({}):
        require(exists('users', {'_id': s['ownerId']}), 'survey.owner')
        if s['publishedVersion'] is not None:
            require(exists('survey_versions', {'surveyId': s['_id'], 'publishedVersion': s['publishedVersion']}), 'survey.currentSnapshot')
    for v in database.survey_versions.find({}):
        require(exists('surveys', {'_id': v['surveyId']}), 'snapshot.survey')
    for collection, field in (('surveys','draft'),('survey_versions','definition')):
        for document in database[collection].find({}):
            for q in document[field]['questions']:
                try:
                    check_binding(q, None, database)
                except (Problem,KeyError,TypeError) as exc:
                    raise RuntimeError('关联完整性失败：已有绑定内容/映射不可信') from exc
    for a in database.attempts.find({}):
        require(exists('users', {'_id': a['userId']}) and exists('survey_versions', {'surveyId': a['surveyId'], 'publishedVersion': a['publishedVersion']}), 'attempt.snapshot/user')
        if a['responseId'] is not None:
            require(exists('responses', {'_id': a['responseId'], 'attemptId': a['_id']}), 'attempt.response')
    for guard in database.participation_guards.find({}):
        require(exists('surveys', {'_id': guard['surveyId']}) and exists('users', {'_id': guard['userId']}), 'guard.survey/user')
    for r in database.responses.find({}):
        require(r['schemaVersion'] == 2, 'responses v1 requires separate sequence migration')
        guard = database.participation_guards.find_one({'_id': r['participationId']})
        require(guard and guard['surveyId'] == r['surveyId'] and isinstance(guard['userId'], ObjectId), 'response.guard')
        require(exists('survey_versions', {'surveyId': r['surveyId'], 'publishedVersion': r['publishedVersion']}), 'response.snapshot')
        require(exists('attempts', {'_id': r['attemptId'], 'responseId': r['_id']}), 'response.attempt')
        require(guard['acceptedCount'] >= r['submissionSequence'], 'response.sequence')
    for key in database.submission_keys.find({}):
        require(exists('responses', {'_id': key['responseId'], 'attemptId': key['attemptId']}), 'submission.response')
    if 'question_versions' in infos:
        for r in database.question_resources.find({}):
            require(exists('users', {'_id':r['ownerId']}) and r['createdBy']==r['ownerId'], 'resource.owner')
            versions=list(database.question_versions.find({'resourceId':r['_id']}))
            require(len(versions)==r['versionCounter'], 'resource.versionCounter')
            require((not versions and r['latestVersionId'] is None) or any(
                x['_id']==r['latestVersionId'] and x['versionNo']==r['versionCounter'] for x in versions), 'resource.latest')
        for v in database.question_versions.find({}):
            r=database.question_resources.find_one({'_id':v['resourceId']})
            require(r and r['ownerId']==v['createdBy'] and v['contentHash']==canonical_hash(v['content']), 'version.resource/content')
            if v['parentVersionId'] is not None:
                parent=database.question_versions.find_one({'_id':v['parentVersionId'],'resourceId':v['resourceId']})
                require(parent and parent['versionNo']<v['versionNo'], 'version.parent')
            if v.get('derivedFrom'):
                src=v['derivedFrom']
                require(exists('question_versions', {'_id':src['questionVersionId'],'resourceId':src['resourceId']}), 'version.derivedFrom')
            if v.get('restoredFromVersionId'):
                require(exists('question_versions', {'_id':v['restoredFromVersionId'],'resourceId':v['resourceId']}), 'version.restoreSource')
            if v['originKind'] == 'ADOPT':
                src = v.get('adoptionSource', {})
                require(src.get('sourceQuestionSnapshot') and src['sourceQuestionHash'] == canonical_hash(src['sourceQuestionSnapshot'])
                        and src['questionId'] == src['sourceQuestionSnapshot']['questionId'], 'ADOPT.sourceEvidence')
                require(exists('surveys', {'_id':src['surveyId'],'ownerId':v['createdBy']}), 'ADOPT.sourceSurvey')
        for b in database.question_banks.find({}):
            require(exists('users', {'_id':b['ownerId']}), 'bank.owner')
        for b in database.question_bank_members.find({}):
            require(exists('question_banks', {'_id':b['bankId']}) and exists('question_versions',
                {'_id':b['questionVersionId'],'resourceId':b['resourceId']}), 'bank.member')
        for grant in database.question_grants.find({}):
            require(exists('users', {'_id':grant['granteeId']}) and exists('question_resources',
                {'_id':grant['resourceId'],'ownerId':grant['grantorId']}) and exists('question_versions',
                {'_id':grant['questionVersionId'],'resourceId':grant['resourceId']}), 'questionGrant.references')
        for grant in database.usage_summary_grants.find({}):
            require(exists('surveys', {'_id':grant['surveyId'],'ownerId':grant['grantorId']}) and exists('question_resources',
                {'_id':grant['resourceId'],'ownerId':grant['recipientId']}), 'summaryGrant.references')
        for u in database.question_usages.find({}):
            survey=database.surveys.find_one({'_id':u['surveyId'],'ownerId':u['surveyOwnerId']})
            snapshot=database.survey_versions.find_one({'surveyId':u['surveyId'],'publishedVersion':u['snapshotVersion']}) if u['snapshotVersion'] else None
            definition=snapshot['definition'] if snapshot else survey['draft'] if survey and not u['snapshotVersion'] else None
            require(survey and definition and any(q['questionId']==u['questionId'] and
                q.get('reuseBinding',{}).get('questionVersionId')==u['questionVersionId'] and
                q.get('reuseBinding',{}).get('resourceId')==u['resourceId'] for q in definition['questions']), 'usage.actualBinding')
        for receipt in database.operation_keys.find({}):
            require(exists('users', {'_id':receipt['actorId']}), 'operation.actor')
    return {'database': database.name, 'collections': len(infos), 'preflight': 'PASS', 'historyDeleted': False}


def logical_hashes(database, session=None):
    result = {}
    for name in BASE_NAMES:
        h = hashlib.sha256(); count = 0
        for doc in database[name].find({}, session=session).sort('_id', 1):
            h.update(canonical_hash(doc).encode()); count += 1
        result[name] = {'count': count, 'sha256': h.hexdigest()}
    return result


def backup(database, folder):
    preflight(database)
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    infos = list(database.list_collections())
    manifest = {'database': database.name, 'planId': PLAN, 'planHash': PLAN_HASH,
                'capturedAt': datetime.now(timezone.utc), 'files': {}, 'ddl': {}, 'hashes': {}}
    # Frozen writer window also stabilizes DDL. Snapshot protects all data reads.
    with database.client.start_session() as session:
        with session.start_transaction(read_concern=ReadConcern('snapshot')):
            for info in infos:
                name = info['name']; p = folder / (name + '.bson')
                with p.open('wb') as f:
                    for doc in database[name].find({}, session=session).sort('_id', 1):
                        f.write(BSON.encode(doc))
                manifest['files'][name] = {'file': p.name, 'sha256': digest_file(p)}
                manifest['ddl'][name] = {'options': info.get('options', {}), 'indexes': list(database[name].list_indexes())}
            manifest['hashes'] = logical_hashes(database, session)
    save(folder / 'manifest.json', manifest)
    return folder / 'manifest.json'


def load_backup(database, path):
    path = Path(path).resolve()
    m = json_util.loads(path.read_text('utf-8'))
    if m['database'] != database.name or m['planHash'] != PLAN_HASH:
        raise RuntimeError('备份数据库/计划哈希不匹配。')
    for info in m['files'].values():
        p = (path.parent / info['file']).resolve()
        if p.parent != path.parent or digest_file(p) != info['sha256']:
            raise RuntimeError('备份文件路径/哈希无效。')
    return m


def assert_history(database, manifest):
    if logical_hashes(database) != manifest['hashes']:
        raise RuntimeError('旧8集合内容变化，停止操作；保留数据排查。注意sessions自然TTL过期需单独审查并重新制作基线。')


def apply(database, path):
    try:
        return apply_steps(database, path)
    except Exception as exc:
        # Only annotate this exact unactivated plan; never alter a foreign/drifted run.
        if 'migration_runs' in database.list_collection_names():
            try:
                database.migration_runs.update_one({'planId':PLAN,'planHash':PLAN_HASH,
                    'backupManifestId':str(Path(path).resolve()),'state':{'$nin':['ACTIVATED','ROLLED_BACK']}},
                    {'$set':{'state':'FAILED','errorCode':type(exc).__name__,'updatedAt':datetime.now(timezone.utc)}})
            except Exception:
                pass  # Preserve original failure if the database itself is unavailable.
        raise


def apply_steps(database, path):
    manifest = load_backup(database, path)
    preflight(database, partial=True)
    assert_history(database, manifest)
    # Create operational schema first so every subsequent DDL is resumable.
    ordered = ['migration_runs', 'migration_items'] + [n for n in NEW_NAMES if n not in OPERATIONAL]
    for name in ordered:
        cfg = TARGET['collections'][name]
        info = next(database.list_collections(filter={'name': name}), None)
        if info is None:
            database.create_collection(name, validator=cfg['validator'], validationLevel='strict', validationAction='error')
        elif not same_options(info, cfg):
            raise RuntimeError('新集合结构冲突：' + name)
        for index in cfg['indexes']:
            database[name].create_index(list(index['key'].items()), **{k: v for k, v in index.items() if k != 'key'})
        if name == 'migration_runs':
            now = datetime.now(timezone.utc)
            run = database.migration_runs.find_one({'planId': PLAN})
            if run and (run['planHash'] != PLAN_HASH or run['backupManifestId'] != str(Path(path).resolve())):
                raise RuntimeError('同一迁移计划参数发生变化。')
            if run and run['state'] == 'ACTIVATED':
                raise RuntimeError('已经激活；不能把再次apply当作激活后升级。')
            database.migration_runs.update_one({'planId': PLAN}, {'$setOnInsert': {
                'schemaVersion': 1, 'planId': PLAN, 'planHash': PLAN_HASH,
                'sourceSchemaHash': canonical_hash(BASE), 'targetSchemaHash': canonical_hash(TARGET),
                'backupManifestId': str(Path(path).resolve()), 'state': 'PREFLIGHT', 'checkpoint': {}, 'startedAt': now,
                'updatedAt': now}}, upsert=True)
    for name in ('surveys', 'survey_versions'):
        cfg = TARGET['collections'][name]
        database.command({'collMod': name, 'validator': cfg['validator'], 'validationLevel': 'strict', 'validationAction': 'error'})
    # Only true existing bindings can generate projection rows. No adoption/inference.
    for collection, definition_field in (('surveys', 'draft'), ('survey_versions', 'definition')):
        for source in database[collection].find({}):
            source_hash = canonical_hash(source)
            key = {'planId': PLAN, 'sourceCollection': collection, 'sourceId': source['_id'], 'stage': 'USAGE_PROJECTION'}
            def project(session):
                item = database.migration_items.find_one(key, session=session)
                if item and item['sourceHash'] != source_hash:
                    raise RuntimeError('源内容变化，不能续跑。')
                sid = source['_id'] if collection == 'surveys' else source['surveyId']
                survey = database.surveys.find_one({'_id': sid}, session=session)
                ids=[]
                for q in source[definition_field]['questions']:
                    b = q.get('reuseBinding')
                    if not b:
                        continue
                    v = database.question_versions.find_one({'_id': b['questionVersionId'], 'resourceId': b['resourceId']}, session=session)
                    if not v or v['contentHash'] != b['contentHash']:
                        raise RuntimeError('已有绑定不可信，不能补投影。')
                    uk={'surveyId': sid, 'snapshotVersion': 0 if collection == 'surveys' else source['publishedVersion'], 'questionId': q['questionId']}
                    payload={'schemaVersion': 1, **uk, 'surveyOwnerId': survey['ownerId'], 'resourceId': b['resourceId'],
                        'questionVersionId': b['questionVersionId'], 'definitionVersion': source['definitionVersion'] if collection == 'surveys' else source['sourceDefinitionVersion']}
                    previous=database.question_usages.find_one(uk,session=session)
                    if previous and any(previous[k]!=v for k,v in payload.items()):
                        raise RuntimeError('使用投影业务键冲突。')
                    database.question_usages.update_one(uk,{'$setOnInsert':{**payload,'createdAt':datetime.now(timezone.utc)}},upsert=True,session=session)
                    ids.append(database.question_usages.find_one(uk,session=session)['_id'])
                database.migration_items.update_one(key, {'$setOnInsert': {'schemaVersion': 1, **key, 'sourceHash': source_hash,
                    'state': 'VERIFIED', 'createdIds': ids}, '$set': {'updatedAt': datetime.now(timezone.utc)}},upsert=True,session=session)
                database.migration_runs.update_one({'planId':PLAN},{'$set':{'checkpoint':{'collection':collection,'lastId':source['_id']},
                    'updatedAt':datetime.now(timezone.utc)}},session=session)
            with database.client.start_session() as session:
                session.with_transaction(project,read_concern=ReadConcern('snapshot'),write_concern=WriteConcern('majority'))
    database.migration_runs.update_one({'planId':PLAN},{'$set':{'state':'DDL_READY','updatedAt':datetime.now(timezone.utc)}})
    assert_history(database,manifest)
    return {'state':'DDL_READY','activationRequired':True,'historyUnchanged':True}


def verify(database,path):
    m=load_backup(database,path);preflight(database);assert_history(database,m)
    for name,cfg in TARGET['collections'].items():
        info=next(database.list_collections(filter={'name':name}),None)
        if not info or not same_options(info,cfg):
            raise RuntimeError('目标DDL尚不完整。')
        check_indexes(database,name,cfg)
    database.migration_runs.update_one({'planId':PLAN,'state':{'$nin':['ACTIVATED','ROLLED_BACK']}},
        {'$set':{'state':'VERIFIED','updatedAt':datetime.now(timezone.utc)}})
    run=database.migration_runs.find_one({'planId':PLAN})
    if not run:
        raise RuntimeError('缺少迁移台账，不能激活。')
    return {'state':run['state'],'historyUnchanged':True}


def activate(database,path):
    if verify(database,path)['state']=='ACTIVATED':
        return {'state':'ACTIVATED','replayed':True}
    changed=database.migration_runs.update_one({'planId':PLAN,'state':'VERIFIED'},
        {'$set':{'state':'ACTIVATED','updatedAt':datetime.now(timezone.utc)}})
    if not changed.matched_count:
        raise RuntimeError('计划未处于VERIFIED，不能激活。')
    return {'state':'ACTIVATED'}


def rollback(database,path):
    m=load_backup(database,path);preflight(database,partial=True);assert_history(database,m)
    run=database.migration_runs.find_one({'planId':PLAN}) if 'migration_runs' in database.list_collection_names() else None
    if run and run['state']=='ACTIVATED':
        raise RuntimeError('已激活：保全新增数据并用兼容应用接管，不允许自动降级。')
    for name in NEW_NAMES:
        if name not in OPERATIONAL and database[name].count_documents({}):
            raise RuntimeError('存在新业务/投影记录：保留全部数据，需另审精确回退，禁止drop。')
    evidence={'database':database.name,'planId':PLAN,'capturedAt':datetime.now(timezone.utc),
              'state':'ROLLBACK_REQUESTED','historyHashes':logical_hashes(database),
              'ledger':{name:list(database[name].find({})) for name in OPERATIONAL}}
    evidence_path=Path(path).resolve().parent/('rollback-ledger-'+ObjectId().__str__()+'.ejson')
    save(evidence_path,evidence)
    for name in ('surveys','survey_versions'):
        cfg=BASE['collections'][name]
        database.command({'collMod':name,'validator':cfg['validator'],'validationLevel':'strict','validationAction':'error'})
    # Only empty, plan-created structures may be removed; historical collections never dropped.
    original=set(m['ddl'])
    for name in reversed(NEW_NAMES):
        if name not in original:
            database.drop_collection(name)
    evidence['state']='ROLLED_BACK';save(evidence_path,evidence)
    return {'state':'ROLLED_BACK','historyUnchanged':True,'evidence':str(evidence_path)}


def restore_copy(database,path,target_name):
    """Restore to a NEW named rehearsal database; never overwrites a live database."""
    m=load_backup(database,path)
    if target_name==database.name or target_name in database.client.list_database_names() or target_name in ('admin','config','local','test'):
        raise RuntimeError('恢复演练目标必须是不存在的新专用数据库。')
    dest=database.client[target_name]
    for name,info in m['ddl'].items():
        dest.create_collection(name,**info['options'])
        with (Path(path).resolve().parent/m['files'][name]['file']).open('rb') as f:
            batch=[]
            for doc in decode_file_iter(f):
                batch.append(doc)
                if len(batch)==500:
                    dest[name].insert_many(batch);batch=[]
            if batch:dest[name].insert_many(batch)
        for index in info['indexes']:
            if index['name']!='_id_':
                dest[name].create_index(list(index['key'].items()),**{k:v for k,v in index.items() if k not in ('key','v','ns')})
    if logical_hashes(dest)!=m['hashes']:
        raise RuntimeError('恢复副本哈希不一致；保留副本排查。')
    return {'restoredDatabase':target_name,'hashesVerified':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['preflight','plan','backup','apply','verify','activate','rollback','restore-copy'])
    p.add_argument('--uri',default=os.getenv('MONGODB_URI','mongodb://127.0.0.1:27018/?replicaSet=survey-rs'))
    p.add_argument('--database',required=True)
    p.add_argument('--maintenance',action='store_true',help='确认所有应用写入已停止；工具不能替你冻结其他进程')
    p.add_argument('--backup',help='manifest.json的路径')
    p.add_argument('--output',help='backup新目录，必须不存在')
    p.add_argument('--target-database',help='restore-copy新副本名')
    args=p.parse_args()
    with MongoClient(args.uri,tz_aware=True,serverSelectionTimeoutMS=5000) as client:
        database=client[args.database]
        if args.command=='plan':
            result={'planId':PLAN,'planHash':PLAN_HASH,'commands':['preflight','backup','restore-copy','apply','verify','activate'],
                    'oldBusinessDocumentsRewritten':False,'newCollections':NEW_NAMES}
        elif args.command=='preflight':result=preflight(database,partial=True)
        else:
            if not args.maintenance:p.error('写入/备份命令必须携带--maintenance并先停写')
            if args.command=='backup':
                if not args.output:p.error('backup需要--output新目录')
                result={'backupManifest':str(backup(database,args.output))}
            else:
                if not args.backup:p.error('需要--backup manifest.json')
                if args.command=='restore-copy':
                    if not args.target_database:p.error('需要--target-database')
                    result=restore_copy(database,args.backup,args.target_database)
                else:result=globals()[args.command](database,args.backup)
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
