"""Shared phase-two invariants; no changes to authentication or legacy metrics."""
from copy import deepcopy
from functools import wraps
import hashlib
import re

from bson import ObjectId, json_util
from bson.int64 import Int64
from flask import current_app, g, request

from .common import fail, fields, fingerprint, plain, require_match, text_field, utcnow
from .domain import new_id, question, validate_definition
from .security import db


def enabled(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        if current_app.extensions['database'].schema_stage != 2:
            fail('SCHEMA_UPGRADE_REQUIRED', '旧数据库继续支持原功能；使用新增功能前请按README_PHASE2显式升级', 409)
        return fn(*args, **kwargs)
    return call


def canonical_hash(value):
    data = json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS,
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def etag(kind, doc):
    return f'"{kind}-{doc["revision"]}"'


def matched(kind, doc):
    if require_match(r'"' + re.escape(kind) + r'-\d+"') != etag(kind, doc):
        fail('VERSION_CONFLICT', '对象已变更，请重新加载', 409)


def cas(collection, doc, changes, session):
    now = utcnow()
    result = collection.update_one({'_id': doc['_id'], 'revision': doc['revision']},
        {'$set': {**changes, 'updatedAt': now}, '$inc': {'revision': Int64(1)}}, session=session)
    if not result.matched_count:
        fail('VERSION_CONFLICT', '对象已变更，请重新加载', 409)
    return {**doc, **changes, 'updatedAt': now, 'revision': Int64(doc['revision'] + 1)}


def idempotent(use_case, data, callback):
    key = request.headers.get('Idempotency-Key', '')
    if not 16 <= len(key) <= 128 or not key.isascii() or any(not 33 <= ord(c) <= 126 for c in key):
        fail('IDEMPOTENCY_REQUIRED', 'Idempotency-Key应为16～128位可打印ASCII', 400)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    fp = fingerprint({'path': request.path, 'body': plain(data), 'ifMatch': request.headers.get('If-Match')})
    def operation(session):
        query = {'actorId': g.user['_id'], 'useCase': use_case, 'keyHash': key_hash}
        found = db().operation_keys.find_one(query, session=session)
        if found:
            if found['requestFingerprint'] != fp:
                fail('IDEMPOTENCY_CONFLICT', '同一幂等键不能用于不同请求', 409)
            return deepcopy(found['result']), True
        result = callback(session)
        db().operation_keys.insert_one({'schemaVersion': 1, **query, 'requestFingerprint': fp,
                                        'result': result, 'createdAt': utcnow()}, session=session)
        return result, False
    return current_app.extensions['database'].transaction(operation)


def content(data, existing=None, fixed=True):
    fields(data, ('type', 'title', 'options', 'validation', 'semantics'), ('type', 'title'))
    kind = data['type']
    local = {k: deepcopy(v) for k, v in data.items() if k != 'semantics'}
    previous = None
    if existing and existing['type'] == kind:
        previous = {'questionId': 'content', 'required': False, 'jumpRules': [],
                    **{k: deepcopy(v) for k, v in existing.items() if k != 'semantics'}}
        if 'options' in previous:
            previous['options'] = [{'optionId': x['semanticOptionId'], 'label': x['label']} for x in previous['options']]
    if 'options' in local:
        if not isinstance(local['options'], list):
            fail('OPTIONS_INVALID', '选项必须为数组')
        for opt in local['options']:
            fields(opt, ('semanticOptionId', 'label'), ('label',))
        local['options'] = [{**({'optionId': x['semanticOptionId']} if 'semanticOptionId' in x else {}),
                             'label': x['label']} for x in local['options']]
    q = question(local, previous)
    validate_definition({'title': 'content', 'questions': [q]}, publishing=fixed)
    result = {k: deepcopy(q[k]) for k in ('type', 'title', 'validation')}
    if 'options' in q:
        result['options'] = [{'semanticOptionId': x['optionId'], 'label': x['label']} for x in q['options']]
    if 'semantics' in data:
        s = data['semantics']
        fields(s, ('measure', 'unit', 'timeBasis', 'population', 'notes'))
        result['semantics'] = {k: text_field(v, k, 2000 if k == 'notes' else 128 if k == 'unit' else 500)
                               for k, v in s.items()}
    return result


def resource(rid, session=None, own=False):
    from .common import oid
    r = db().question_resources.find_one({'_id': oid(rid)}, session=session)
    if not r or (own and r['ownerId'] != g.user['_id']):
        fail('NOT_FOUND', '题目不存在或无权访问', 404)
    if r['ownerId'] != g.user['_id'] and not db().question_grants.find_one(
            {'resourceId': r['_id'], 'granteeId': g.user['_id']}, session=session):
        fail('NOT_FOUND', '题目不存在或无权访问', 404)
    return r


def version(r, vid, session=None):
    from .common import oid
    v = db().question_versions.find_one({'_id': oid(vid), 'resourceId': r['_id']}, session=session)
    if not v or (r['ownerId'] != g.user['_id'] and not db().question_grants.find_one(
            {'questionVersionId': v['_id'], 'resourceId': r['_id'], 'granteeId': g.user['_id']}, session=session)):
        fail('NOT_FOUND', '版本不存在或无权访问', 404)
    return v


def new_resource(c, fixed, *, identity=None, version_id=None, origin='ORIGINAL', source=None, adoption=None, session):
    rid, vid, now = identity or ObjectId(), version_id or ObjectId(), utcnow()
    r = {'_id': rid, 'schemaVersion': 1, 'ownerId': g.user['_id'], 'createdBy': g.user['_id'],
         'revision': Int64(0), 'latestVersionId': vid if fixed else None,
         'versionCounter': Int64(1 if fixed else 0), 'createdAt': now, 'updatedAt': now}
    if fixed:
        v = {'_id': vid, 'schemaVersion': 1, 'resourceId': rid, 'versionNo': Int64(1),
             'createdBy': g.user['_id'], 'parentVersionId': None, 'originKind': origin,
             'content': deepcopy(c), 'contentHash': canonical_hash(c), 'createdAt': now}
        if source:
            v['derivedFrom'] = source
        if adoption:
            v['adoptionSource'] = adoption
        db().question_versions.insert_one(v, session=session)
    else:
        r['workingDraft'] = deepcopy(c)
    db().question_resources.insert_one(r, session=session)
    return r


def check_binding(q, session, database=None):
    binding = q.get('reuseBinding')
    if not binding:
        return
    storage = database if database is not None else db()
    v = storage.question_versions.find_one({'_id': binding['questionVersionId'], 'resourceId': binding['resourceId']}, session=session)
    if not v or v['contentHash'] != binding['contentHash'] or canonical_hash(v['content']) != v['contentHash']:
        fail('BINDING_INVALID', '绑定版本或哈希不一致', 422)
    c = v['content']
    if any(q[k] != c[k] for k in ('type', 'title', 'validation')) or binding['semanticsSnapshot'] != c.get('semantics', {}):
        fail('BINDING_INVALID', '本卷内容与固定版本不一致')
    mapping = binding['optionMap']
    pairs = {x['semanticOptionId']: x['localOptionId'] for x in mapping}
    expected = [{'optionId': pairs.get(x['semanticOptionId']), 'label': x['label']} for x in c.get('options', [])]
    if (len(pairs) != len(mapping) or len(set(pairs.values())) != len(mapping) or
            set(pairs) != {x['semanticOptionId'] for x in c.get('options', [])} or q.get('options', []) != expected):
        fail('BINDING_INVALID', '选项映射必须完整且一一对应')


def assemble(v, old=None, supplied_map=None):
    c = v['content']
    q = {'questionId': old['questionId'] if old else new_id('q'), 'required': old['required'] if old else False,
         'jumpRules': deepcopy(old['jumpRules']) if old else [],
         **{k: deepcopy(c[k]) for k in ('type', 'title', 'validation')}}
    old_map = {x['semanticOptionId']: x['localOptionId'] for x in (old or {}).get('reuseBinding', {}).get('optionMap', [])}
    if supplied_map is not None:
        if not isinstance(supplied_map, list):
            fail('BINDING_INVALID', 'optionMap必须为数组')
        pairs = {}
        for x in supplied_map:
            fields(x, ('semanticOptionId', 'localOptionId'), ('semanticOptionId', 'localOptionId'))
            pairs[text_field(x['semanticOptionId'], 'semanticOptionId', 64, 1)] = text_field(x['localOptionId'], 'localOptionId', 64, 1)
        if len(pairs) != len(supplied_map) or len(set(pairs.values())) != len(pairs):
            fail('BINDING_INVALID', '选项映射不能重复')
    else:
        pairs = {x['semanticOptionId']: old_map.get(x['semanticOptionId'], new_id('opt')) for x in c.get('options', [])}
    if set(pairs) != {x['semanticOptionId'] for x in c.get('options', [])}:
        fail('BINDING_INVALID', '选项映射不完整')
    if 'options' in c:
        q['options'] = [{'optionId': pairs[x['semanticOptionId']], 'label': x['label']} for x in c['options']]
    q['reuseBinding'] = {'resourceId': v['resourceId'], 'questionVersionId': v['_id'], 'contentHash': v['contentHash'],
                         'optionMap': [{'semanticOptionId': k, 'localOptionId': val} for k, val in pairs.items()],
                         'semanticsSnapshot': deepcopy(c.get('semantics', {}))}
    return q


def sync_usages(s, session, snapshot_version=0):
    questions = [q for q in s['draft']['questions'] if q.get('reuseBinding')]
    scope = {'surveyId': s['_id'], 'snapshotVersion': snapshot_version}
    if not snapshot_version:
        db().question_usages.delete_many({**scope, 'questionId': {'$nin': [q['questionId'] for q in questions]}}, session=session)
    for q in questions:
        binding = q['reuseBinding']
        payload = {'surveyOwnerId': s['ownerId'], 'resourceId': binding['resourceId'],
                   'questionVersionId': binding['questionVersionId'], 'definitionVersion': s['definitionVersion']}
        key = {**scope, 'questionId': q['questionId']}
        if snapshot_version:
            existing = db().question_usages.find_one(key, session=session)
            if existing and any(existing[k] != val for k, val in payload.items()):
                fail('DATA_INTEGRITY_ERROR', '历史使用关系不一致', 500)
            db().question_usages.update_one(key, {'$setOnInsert': {'schemaVersion': 1, **key, **payload, 'createdAt': utcnow()}}, upsert=True, session=session)
        else:
            db().question_usages.update_one(key, {'$set': payload, '$setOnInsert': {'schemaVersion': 1, **key, 'createdAt': utcnow()}}, upsert=True, session=session)
