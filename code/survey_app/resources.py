"""Immutable selectable question versions and owner-managed working drafts."""
from copy import deepcopy

from bson import ObjectId
from bson.int64 import Int64
from flask import Blueprint, current_app, g, request

from .common import body, fail, oid, page_limit, reply, utcnow
from .phase2_core import (enabled, content, resource, version, new_resource, canonical_hash,
                          cas, etag, matched, idempotent)
from .security import authenticated, db

resources = Blueprint('resources', __name__, url_prefix='/api/v1')


def version_view(v, owner):
    allowed = ('_id', 'resourceId', 'versionNo', 'parentVersionId', 'originKind', 'content', 'contentHash',
               'derivedFrom', 'restoredFromVersionId', 'createdAt')
    result = {('questionVersionId' if k == '_id' else k): deepcopy(v[k]) for k in allowed if k in v}
    if owner and 'adoptionSource' in v:
        result['adoptionSource'] = deepcopy(v['adoptionSource'])
    return result


def resource_view(r, owner):
    result = {'resourceId': r['_id'], 'ownership': 'SELF' if owner else 'SHARED', 'createdAt': r['createdAt'],
              'updatedAt': r['updatedAt']}
    if owner:
        result.update(latestVersionId=r['latestVersionId'], versionCounter=r['versionCounter'])
        if 'workingDraft' in r:
            result['workingDraft'] = r['workingDraft']
    return result


def paged(query, collection, scope, *, sort=None):
    limit = page_limit()
    cursors = current_app.extensions['cursors']
    before = cursors.decode(request.args.get('cursor'), scope)
    if before:
        query = {'$and': [query, {'_id': {'$lt': oid(before)}}]}
    rows = list(collection.find(query).sort(sort or [('_id', -1)]).limit(limit + 1))
    more, rows = len(rows) > limit, rows[:limit]
    return rows, cursors.encode(scope, rows[-1]['_id']) if more else None


@resources.get('/capabilities')
@authenticated
def capabilities():
    return reply({'schemaStage': current_app.extensions['database'].schema_stage,
                  'phase2Available': current_app.extensions['database'].schema_stage == 2})


@resources.post('/question-resources')
@authenticated
@enabled
def create_resource():
    data = body(('content', 'fixed'), ('content',))
    fixed = data.get('fixed', True)
    if type(fixed) is not bool:
        fail('FIELD_INVALID', 'fixed必须是布尔值')
    c = content(data['content'], fixed=fixed)
    rid, vid = ObjectId(), ObjectId()
    def operation(session):
        r = new_resource(c, fixed, identity=rid, version_id=vid, session=session)
        result = {'resourceId': r['_id'], 'resultEtag': etag('resource', r)}
        if fixed:
            result['questionVersionId'] = vid
        return result
    result, replay = idempotent('CREATE_RESOURCE', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])


@resources.get('/question-resources')
@authenticated
@enabled
def list_resources():
    scope = request.args.get('scope', 'OWN')
    if scope not in ('OWN', 'SHARED'):
        fail('SCOPE_INVALID', 'scope应为OWN或SHARED', 400)
    query = {'ownerId': g.user['_id']}
    if scope == 'SHARED':
        ids = db().question_grants.distinct('resourceId', {'granteeId': g.user['_id']})
        query = {'_id': {'$in': ids}, 'ownerId': {'$ne': g.user['_id']}}
    rows, cursor = paged(query, db().question_resources,
        {'kind': 'resources', 'owner': str(g.user['_id']), 'scope': scope})
    return reply({'items': [resource_view(x, scope == 'OWN') for x in rows], 'nextCursor': cursor})


@resources.get('/question-resources/<rid>')
@authenticated
@enabled
def resource_detail(rid):
    r = resource(rid)
    owner = r['ownerId'] == g.user['_id']
    return reply(resource_view(r, owner), etag=etag('resource', r) if owner else None)


@resources.patch('/question-resources/<rid>/draft')
@authenticated
@enabled
def save_draft(rid):
    data = body(('content',), ('content',))
    def operation(session):
        r = resource(rid, session, own=True)
        matched('resource', r)
        c = content(data['content'], r.get('workingDraft'), fixed=False)
        return cas(db().question_resources, r, {'workingDraft': c}, session)
    r = current_app.extensions['database'].transaction(operation)
    return reply(resource_view(r, True), etag=etag('resource', r))


def fixed_successor(rid, data, restore=False):
    vid = ObjectId()
    def operation(session):
        r = resource(rid, session, own=True)
        matched('resource', r)
        parent_id = data.get('baseVersionId')
        parent = version(r, parent_id, session) if parent_id else None
        if r['versionCounter'] and not parent:
            fail('BASE_REQUIRED', '生成后继必须明确baseVersionId')
        if restore:
            source = version(r, data['restoreFromVersionId'], session)
            c = deepcopy(source['content'])
        else:
            raw = data.get('content')
            if raw is None:
                if not r.get('workingDraft'):
                    fail('CONTENT_REQUIRED', '没有工作稿，请提供content')
                from .common import plain
                raw = plain(r['workingDraft'])
            c = content(raw, parent['content'] if parent else r.get('workingDraft'))
        v = {'_id': vid, 'schemaVersion': 1, 'resourceId': r['_id'], 'versionNo': Int64(r['versionCounter'] + 1),
             'createdBy': g.user['_id'], 'parentVersionId': parent['_id'] if parent else None,
             'originKind': 'RESTORE' if restore else 'EDIT' if parent else 'ORIGINAL',
             'content': c, 'contentHash': canonical_hash(c), 'createdAt': utcnow()}
        if restore:
            v['restoredFromVersionId'] = source['_id']
        db().question_versions.insert_one(v, session=session)
        updated = cas(db().question_resources, r, {'latestVersionId': vid, 'versionCounter': v['versionNo']}, session)
        return {'resourceId': r['_id'], 'questionVersionId': vid, 'resultEtag': etag('resource', updated)}
    result, replay = idempotent('RESTORE' if restore else 'FIX_VERSION', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])


@resources.post('/question-resources/<rid>/versions')
@authenticated
@enabled
def fix_version(rid):
    return fixed_successor(rid, body(('baseVersionId', 'content')))


@resources.post('/question-resources/<rid>/restorations')
@authenticated
@enabled
def restore(rid):
    return fixed_successor(rid, body(('baseVersionId', 'restoreFromVersionId'), ('baseVersionId', 'restoreFromVersionId')), True)


@resources.get('/question-resources/<rid>/versions')
@authenticated
@enabled
def versions(rid):
    r = resource(rid)
    owner = r['ownerId'] == g.user['_id']
    query = {'resourceId': r['_id']}
    if not owner:
        query['_id'] = {'$in': db().question_grants.distinct('questionVersionId', {'resourceId': r['_id'], 'granteeId': g.user['_id']})}
    rows, cursor = paged(query, db().question_versions, {'kind': 'versions', 'owner': str(g.user['_id']), 'resource': rid})
    return reply({'items': [version_view(x, owner) for x in rows], 'nextCursor': cursor})


@resources.get('/question-resources/<rid>/versions/<vid>')
@authenticated
@enabled
def version_detail(rid, vid):
    r = resource(rid)
    return reply(version_view(version(r, vid), r['ownerId'] == g.user['_id']))


@resources.get('/question-resources/<rid>/lineage')
@authenticated
@enabled
def lineage(rid):
    # Same bounded, authorized version page; no traversal into another private resource.
    return versions(rid)


@resources.post('/question-resources/<rid>/versions/<vid>/forks')
@authenticated
@enabled
def fork(rid, vid):
    data = body(('content',))
    identity, version_id = ObjectId(), ObjectId()
    def operation(session):
        r = resource(rid, session)
        v = version(r, vid, session)
        c = content(data['content'], v['content']) if 'content' in data else v['content']
        created = new_resource(c, True, identity=identity, version_id=version_id, origin='FORK',
            source={'resourceId': r['_id'], 'questionVersionId': v['_id']}, session=session)
        return {'resourceId': identity, 'questionVersionId': version_id, 'resultEtag': etag('resource', created)}
    result, replay = idempotent('FORK', data, operation)
    return reply({**result, 'replayed': replay}, 200 if replay else 201, result['resultEtag'])
