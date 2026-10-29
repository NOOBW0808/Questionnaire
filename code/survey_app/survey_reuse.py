"""Atomic adoption/import/rebinding use cases on the existing survey editor."""
from copy import deepcopy

from bson import ObjectId
from flask import Blueprint, g

from .common import body, fail, plain, reply, survey_etag, utcnow
from .management import find_question, mutation
from .phase2_core import (enabled, resource, version, content, assemble, canonical_hash,
                          new_resource, idempotent)
from .security import authenticated, db

survey_reuse = Blueprint('survey_reuse', __name__, url_prefix='/api/v1')
CONSENT_FIELDS = ['surveyId', 'surveyTitle', 'ownership', 'resourceId', 'questionVersionId',
                  'publishedVersion', 'usageStage', 'surveyStatus', 'instanceCount']


def consent(s, r, data, session):
    if r['ownerId'] == g.user['_id']:
        return
    key = {'surveyId': s['_id'], 'resourceId': r['_id']}
    existing = db().usage_summary_grants.find_one(key, session=session)
    if existing and existing['enabled'] and existing['policyVersion'] == 'SUMMARY_V1' and existing['recipientId'] == r['ownerId']:
        return
    agreement = data.get('usageSummaryConsent')
    if agreement != {'accepted': True, 'policyVersion': 'SUMMARY_V1'}:
        fail('USAGE_SUMMARY_CONSENT_REQUIRED', '请明确确认向题目所有者公开最小使用摘要；不同意则不导入',
             409, {'policyVersion': 'SUMMARY_V1', 'fields': CONSENT_FIELDS})
    from bson.int64 import Int64
    now = utcnow()
    payload = {**key, 'grantorId': s['ownerId'], 'recipientId': r['ownerId'], 'enabled': True,
               'policyVersion': 'SUMMARY_V1', 'confirmedAt': now, 'updatedAt': now}
    db().usage_summary_grants.update_one(key, {'$set': payload, '$inc': {'revision': Int64(1)},
        '$setOnInsert': {'schemaVersion': 1, 'createdAt': now}}, upsert=True, session=session)


def change_binding(sid, qid=None):
    data = body(('resourceId', 'questionVersionId', 'optionMap', 'usageSummaryConsent'), ('resourceId', 'questionVersionId'))
    def operation(session):
        def change(s, session):
            r = resource(data['resourceId'], session)
            v = version(r, data['questionVersionId'], session)
            consent(s, r, data, session)
            old = find_question(s, qid) if qid else None
            q = assemble(v, old, data.get('optionMap'))
            if old:
                s['draft']['questions'][s['draft']['questions'].index(old)] = q
            else:
                s['draft']['questions'].append(q)
            return s, True
        updated = mutation(sid, change, session)
        q = find_question(updated, qid) if qid else updated['draft']['questions'][-1]
        return {'surveyId': updated['_id'], 'questionId': q['questionId'],
                'resourceId': q['reuseBinding']['resourceId'], 'questionVersionId': q['reuseBinding']['questionVersionId'],
                'resultEtag': survey_etag(updated)}
    if qid:
        from flask import current_app
        result = current_app.extensions['database'].transaction(operation)
        return reply(result, etag=result['resultEtag'])
    result, replay = idempotent('IMPORT', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])


@survey_reuse.post('/surveys/<sid>/question-imports')
@authenticated
@enabled
def import_question(sid):
    return change_binding(sid)


@survey_reuse.put('/surveys/<sid>/questions/<qid>/binding')
@authenticated
@enabled
def rebind(sid, qid):
    return change_binding(sid, qid)


@survey_reuse.post('/surveys/<sid>/questions/<qid>/adoption')
@authenticated
@enabled
def adopt(sid, qid):
    data = body(('semantics',))
    identity, version_id = ObjectId(), ObjectId()
    def operation(session):
        def change(s, session):
            q = find_question(s, qid)
            if q.get('reuseBinding'):
                fail('ALREADY_BOUND', '已有绑定的题目请使用版本或派生功能', 409)
            snapshot = deepcopy(q)
            source = {'sourceKind': 'DRAFT_INSTANCE', 'surveyId': s['_id'], 'questionId': qid,
                      'definitionVersion': s['definitionVersion'], 'controlRevision': s['controlRevision'],
                      'extractedAt': utcnow(), 'sourceQuestionHash': canonical_hash(snapshot), 'sourceQuestionSnapshot': snapshot}
            raw = {k: deepcopy(q[k]) for k in ('type', 'title', 'validation')}
            if 'options' in q:
                raw['options'] = [{'label': x['label']} for x in q['options']]
            if 'semantics' in data:
                raw['semantics'] = data['semantics']
            c = content(plain(raw))
            new_resource(c, True, identity=identity, version_id=version_id, origin='ADOPT', adoption=source, session=session)
            v = db().question_versions.find_one({'_id': version_id}, session=session)
            option_map = [{'semanticOptionId': a['semanticOptionId'], 'localOptionId': b['optionId']}
                          for a, b in zip(c.get('options', []), q.get('options', []))]
            replacement = assemble(v, q, option_map)
            s['draft']['questions'][s['draft']['questions'].index(q)] = replacement
            return s, True
        s = mutation(sid, change, session)
        return {'surveyId': s['_id'], 'questionId': qid, 'resourceId': identity,
                'questionVersionId': version_id, 'resultEtag': survey_etag(s)}
    result, replay = idempotent('ADOPT', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])


@survey_reuse.post('/surveys/<sid>/questions/<qid>/derivations')
@authenticated
@enabled
def derive(sid, qid):
    data = body(('content',), ('content',))
    identity, version_id = ObjectId(), ObjectId()
    def operation(session):
        def change(s, session):
            old = find_question(s, qid)
            binding = old.get('reuseBinding')
            if not binding:
                fail('BINDING_REQUIRED', '未绑定题请先保存为常用题')
            r = resource(str(binding['resourceId']), session)
            v = version(r, str(binding['questionVersionId']), session)
            c = content(data['content'], v['content'])
            new_resource(c, True, identity=identity, version_id=version_id, origin='FORK',
                source={'resourceId': r['_id'], 'questionVersionId': v['_id']}, session=session)
            nv = db().question_versions.find_one({'_id': version_id}, session=session)
            s['draft']['questions'][s['draft']['questions'].index(old)] = assemble(nv, old)
            return s, True
        s = mutation(sid, change, session)
        return {'surveyId': s['_id'], 'questionId': qid, 'resourceId': identity,
                'questionVersionId': version_id, 'resultEtag': survey_etag(s)}
    result, replay = idempotent('DERIVE_AND_BIND', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])
