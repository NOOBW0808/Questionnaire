"""Banks, version grants and separately authorized minimal usage summaries."""
import unicodedata

from bson import ObjectId
from bson.int64 import Int64
from flask import Blueprint, current_app, g, request

from .common import body, boolean, fail, oid, page_limit, reply, text_field, utcnow
from .management import owned, effective_state
from .phase2_core import enabled, resource, version, etag, matched, cas, idempotent
from .resources import paged
from .security import authenticated, db

sharing = Blueprint('sharing', __name__, url_prefix='/api/v1')


def creation_precondition():
    if request.headers.get('If-None-Match') != '*':
        fail('PRECONDITION_REQUIRED', '首次授权请携带If-None-Match: *', 428)


def bank(bid, session=None):
    b = db().question_banks.find_one({'_id': oid(bid), 'ownerId': g.user['_id'], 'state': 'ACTIVE'}, session=session)
    if not b:
        fail('NOT_FOUND', '题库不存在或无权访问', 404)
    return b


def bank_view(b):
    return {'bankId': b['_id'], 'name': b['name'], 'state': b['state'], 'createdAt': b['createdAt'], 'updatedAt': b['updatedAt']}


@sharing.post('/question-banks')
@authenticated
@enabled
def create_bank():
    data = body(('name',), ('name',))
    name = text_field(data['name'], '题库名称', 200, 1)
    now = utcnow()
    b = {'_id': ObjectId(), 'schemaVersion': 1, 'ownerId': g.user['_id'], 'name': name, 'state': 'ACTIVE',
         'revision': Int64(0), 'createdAt': now, 'updatedAt': now}
    def operation(session):
        db().question_banks.insert_one(b, session=session)
        return {'bankId': b['_id'], 'resultEtag': etag('bank', b)}
    result, replay = idempotent('CREATE_BANK', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])


@sharing.get('/question-banks')
@authenticated
@enabled
def banks():
    rows, cursor = paged({'ownerId': g.user['_id'], 'state': 'ACTIVE'}, db().question_banks,
                        {'kind': 'banks', 'owner': str(g.user['_id'])})
    return reply({'items': [bank_view(x) for x in rows], 'nextCursor': cursor})


@sharing.get('/question-banks/<bid>')
@authenticated
@enabled
def bank_detail(bid):
    b = bank(bid)
    rows, cursor = paged({'bankId': b['_id']}, db().question_bank_members,
                        {'kind': 'bank_members', 'owner': str(g.user['_id']), 'bank': bid})
    return reply({**bank_view(b), 'members': [{'resourceId': x['resourceId'], 'questionVersionId': x['questionVersionId']} for x in rows],
                  'nextCursor': cursor}, etag=etag('bank', b))


@sharing.patch('/question-banks/<bid>')
@authenticated
@enabled
def rename_bank(bid):
    data = body(('name',), ('name',))
    def operation(session):
        b = bank(bid, session); matched('bank', b)
        return cas(db().question_banks, b, {'name': text_field(data['name'], '题库名称', 200, 1)}, session)
    b = current_app.extensions['database'].transaction(operation)
    return reply(bank_view(b), etag=etag('bank', b))


@sharing.delete('/question-banks/<bid>')
@authenticated
@enabled
def delete_bank(bid):
    def operation(session):
        b = bank(bid, session); matched('bank', b)
        return cas(db().question_banks, b, {'state': 'DELETED'}, session)
    b = current_app.extensions['database'].transaction(operation)
    return reply(status=204, etag=etag('bank', b))


@sharing.route('/question-banks/<bid>/members/<rid>', methods=['PUT', 'DELETE'])
@authenticated
@enabled
def member(bid, rid):
    data = body(('questionVersionId',), ('questionVersionId',)) if request.method == 'PUT' else {}
    def operation(session):
        b = bank(bid, session); matched('bank', b)
        key = {'bankId': b['_id'], 'resourceId': oid(rid)}
        if request.method == 'PUT':
            r = resource(rid, session); v = version(r, data['questionVersionId'], session)
            now = utcnow()
            db().question_bank_members.update_one(key, {'$set': {'questionVersionId': v['_id'], 'updatedAt': now},
                '$setOnInsert': {'schemaVersion': 1, **key, 'createdAt': now}}, upsert=True, session=session)
        else:
            db().question_bank_members.delete_one(key, session=session)
        return cas(db().question_banks, b, {}, session)
    b = current_app.extensions['database'].transaction(operation)
    return reply(bank_view(b), etag=etag('bank', b))


@sharing.route('/question-resources/<rid>/versions/<vid>/grants', methods=['GET', 'PUT'])
@authenticated
@enabled
def grants(rid, vid):
    data = body(('username',), ('username',)) if request.method == 'PUT' else None
    def operation(session):
        r = resource(rid, session, own=True); v = version(r, vid, session)
        if data is None:
            # A resource owner may inspect this version's grants, not a global user list.
            limit = page_limit(); cursors = current_app.extensions['cursors']
            scope = {'kind': 'grants', 'owner': str(g.user['_id']), 'resource': rid, 'version': vid}
            after = cursors.decode(request.args.get('cursor'), scope)
            query = {'questionVersionId': v['_id']}
            if after:
                query['_id'] = {'$lt': oid(after)}
            rows = list(db().question_grants.find(query, session=session).sort('_id', -1).limit(limit + 1))
            more, rows = len(rows) > limit, rows[:limit]
            return {'items': [{'granteeId': x['granteeId'], 'username': (db().users.find_one({'_id':x['granteeId']}, {'username':1}, session=session) or {}).get('username'),
                              'actions': x['actions'], 'etag': etag('grant', x)} for x in rows],
                    'nextCursor': cursors.encode(scope, rows[-1]['_id']) if more else None}, None
        username = text_field(data['username'], '接收用户名', 64, 1)
        user = db().users.find_one({'usernameKey': unicodedata.normalize('NFKC', username).casefold()}, session=session)
        if not user or user['_id'] == g.user['_id']:
            fail('RECIPIENT_INVALID', '接收账号不存在或为本人', 422)
        key = {'questionVersionId': v['_id'], 'granteeId': user['_id']}
        grant = db().question_grants.find_one(key, session=session)
        if grant:
            matched('grant', grant)
            grant = cas(db().question_grants, grant, {'actions': ['USE', 'FORK']}, session)
        else:
            creation_precondition(); now = utcnow()
            grant = {'_id': ObjectId(), 'schemaVersion': 1, **key, 'resourceId': r['_id'], 'grantorId': g.user['_id'],
                     'actions': ['USE', 'FORK'], 'revision': Int64(0), 'createdAt': now, 'updatedAt': now}
            db().question_grants.insert_one(grant, session=session)
        return {'resourceId': r['_id'], 'questionVersionId': v['_id'], 'granteeId': user['_id'], 'actions': ['USE', 'FORK']}, etag('grant', grant)
    result, tag = current_app.extensions['database'].transaction(operation)
    return reply(result, etag=tag)


@sharing.get('/surveys/<sid>/usage-summary-grants')
@authenticated
@enabled
def summary_grants(sid):
    s = owned(sid)
    rows, cursor = paged({'surveyId': s['_id']}, db().usage_summary_grants,
        {'kind': 'summary_grants', 'owner': str(g.user['_id']), 'survey': sid})
    return reply({'items': [{k: x[k] for k in ('resourceId', 'enabled', 'policyVersion', 'confirmedAt')} for x in rows],
                  'nextCursor': cursor})


@sharing.route('/surveys/<sid>/usage-summary-grants/<rid>', methods=['GET', 'PUT'])
@authenticated
@enabled
def summary_grant(sid, rid):
    data = body(('enabled', 'policyVersion'), ('enabled', 'policyVersion')) if request.method == 'PUT' else None
    def operation(session):
        s = owned(sid, session)
        r = resource(rid, session)
        key = {'surveyId': s['_id'], 'resourceId': r['_id']}
        grant = db().usage_summary_grants.find_one(key, session=session)
        if data is None:
            if not grant:
                fail('NOT_FOUND', '尚无摘要授权', 404)
        else:
            if data['policyVersion'] != 'SUMMARY_V1':
                fail('POLICY_INVALID', '只支持SUMMARY_V1')
            enabled_value = boolean(data['enabled'], 'enabled')
            if r['ownerId'] == g.user['_id']:
                fail('GRANT_UNNECESSARY', '本人问卷使用本人资源不需要摘要授权')
            if grant:
                matched('summary', grant)
                grant = cas(db().usage_summary_grants, grant, {'enabled': enabled_value,
                    'policyVersion': 'SUMMARY_V1', 'confirmedAt': utcnow()}, session)
            else:
                creation_precondition(); now = utcnow()
                grant = {'_id': ObjectId(), 'schemaVersion': 1, **key, 'grantorId': s['ownerId'], 'recipientId': r['ownerId'],
                    'enabled': enabled_value, 'policyVersion': 'SUMMARY_V1', 'revision': Int64(0),
                    'confirmedAt': now, 'createdAt': now, 'updatedAt': now}
                db().usage_summary_grants.insert_one(grant, session=session)
            # A real write competes with import/rebind on the same survey document.
            result = db().surveys.update_one({'_id': s['_id'], 'controlRevision': s['controlRevision']},
                {'$inc': {'controlRevision': Int64(1)}, '$set': {'updatedAt': utcnow()}}, session=session)
            if not result.matched_count:
                fail('VERSION_CONFLICT', '问卷已变更', 409)
        return {k: grant[k] for k in ('surveyId', 'resourceId', 'enabled', 'policyVersion', 'confirmedAt')}, etag('summary', grant)
    result, tag = current_app.extensions['database'].transaction(operation)
    return reply(result, etag=tag)


@sharing.get('/question-resources/<rid>/usages')
@authenticated
@enabled
def usages(rid):
    limit = page_limit()
    filters = {k: request.args.get(k) for k in ('questionVersionId', 'usageStage', 'surveyStatus')}
    if filters['usageStage'] and filters['usageStage'] not in ('DRAFT', 'CURRENT_PUBLISHED', 'HISTORICAL_PUBLISHED'):
        fail('FILTER_INVALID', 'usageStage无效', 400)
    if filters['surveyStatus'] and filters['surveyStatus'] not in ('DRAFT', 'PUBLISHED', 'CLOSED', 'EXPIRED'):
        fail('FILTER_INVALID', 'surveyStatus无效', 400)
    cursors = current_app.extensions['cursors']
    scope = {'kind': 'usages', 'owner': str(g.user['_id']), 'resource': rid, **filters, 'policy': 'SUMMARY_V1'}
    after = cursors.decode(request.args.get('cursor'), scope)
    def operation(session):
        r = resource(rid, session)
        query = {'resourceId': r['_id']}
        if filters['questionVersionId']:
            query['questionVersionId'] = version(r, filters['questionVersionId'], session)['_id']
        groups = {}
        now = utcnow()
        for u in db().question_usages.find(query, session=session).max_time_ms(10000):
            s = db().surveys.find_one({'_id': u['surveyId']}, session=session)
            if not s:
                continue
            self_owned = s['ownerId'] == g.user['_id']
            if not self_owned and (r['ownerId'] != g.user['_id'] or not db().usage_summary_grants.find_one({
                'surveyId': s['_id'], 'resourceId': r['_id'], 'grantorId': s['ownerId'], 'recipientId': g.user['_id'],
                'enabled': True, 'policyVersion': 'SUMMARY_V1'}, session=session)):
                continue
            snapshot = db().survey_versions.find_one({'surveyId': s['_id'], 'publishedVersion': u['snapshotVersion']}, session=session) if u['snapshotVersion'] else None
            definition = snapshot['definition'] if snapshot else s['draft'] if not u['snapshotVersion'] else None
            if not definition or not any(q['questionId'] == u['questionId'] and
                    q.get('reuseBinding', {}).get('resourceId') == r['_id'] and
                    q.get('reuseBinding', {}).get('questionVersionId') == u['questionVersionId'] for q in definition['questions']):
                continue  # Stale projection is not authoritative evidence.
            stage = 'DRAFT' if not u['snapshotVersion'] else 'CURRENT_PUBLISHED' if s['publishedVersion'] == u['snapshotVersion'] else 'HISTORICAL_PUBLISHED'
            status = effective_state(s, now)
            if filters['usageStage'] and stage != filters['usageStage'] or filters['surveyStatus'] and status != filters['surveyStatus']:
                continue
            key = (str(s['_id']), u['snapshotVersion'], str(u['questionVersionId']))
            if key not in groups:
                groups[key] = {'surveyId': s['_id'], 'surveyTitle': definition['title'], 'ownership': 'SELF' if self_owned else 'OTHER',
                    'resourceId': r['_id'], 'questionVersionId': u['questionVersionId'], 'publishedVersion': u['snapshotVersion'] or None,
                    'usageStage': stage, 'surveyStatus': status, 'instanceCount': 0}
            groups[key]['instanceCount'] += 1
        keys = sorted(k for k in groups if not after or k > tuple(after))
        page = keys[:limit]
        return {'items': [groups[k] for k in page], 'coverage': 'SELF_AND_AUTHORIZED_OTHERS', 'totalVisible': len(groups),
                'nextCursor': cursors.encode(scope, list(page[-1])) if len(keys) > limit else None}
    return reply(current_app.extensions['database'].transaction(operation))
