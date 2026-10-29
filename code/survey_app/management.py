from copy import deepcopy

from bson import ObjectId
from bson.int64 import Int64
from flask import Blueprint, current_app, g, request

from .common import (body, boolean, deadline, fail, oid, page_limit, reply, require_match,
                     survey_etag, text_field, utcnow)
from .domain import jump_rules, new_id, question, validate_definition
from .security import authenticated, db

management = Blueprint("management", __name__, url_prefix="/api/v1")


def owned(survey_id, session=None):
    survey = db().surveys.find_one({"_id": oid(survey_id), "ownerId": g.user["_id"]}, session=session)
    if not survey:
        fail("NOT_FOUND", "问卷不存在或无权访问", 404)
    return survey


def effective_state(s, now=None):
    return "EXPIRED" if s["state"] == "PUBLISHED" and s["deadlineAt"] and s["deadlineAt"] <= (now or utcnow()) else s["state"]


def survey_view(s):
    return {"surveyId": s["_id"], "publicCode": s["publicCode"], "state": s["state"],
            "effectiveState": effective_state(s), "definitionVersion": s["definitionVersion"],
            "controlRevision": s["controlRevision"], "publishedVersion": s["publishedVersion"],
            "deadlineAt": s["deadlineAt"], "draft": s["draft"], "createdAt": s["createdAt"],
            "updatedAt": s["updatedAt"], "shareUrl": "/survey/" + s["publicCode"]}


def mutation(survey_id, change, session=None):
    supplied = require_match(r'"definition-\d+-control-\d+"')
    def operation(session):
        s = owned(survey_id, session)
        if supplied != survey_etag(s):
            fail("VERSION_CONFLICT", "问卷已被修改，请重新加载后操作", 409)
        updated, definition_changed = change(deepcopy(s), session)
        validate_definition(updated["draft"])
        from .phase2_core import check_binding, sync_usages
        if current_app.extensions['database'].schema_stage == 2:
            for q in updated['draft']['questions']:
                check_binding(q, session)
            if any(q.get('reuseBinding') for q in updated['draft']['questions']):
                updated['schemaVersion'] = 2
        updated["definitionVersion"] += int(definition_changed)
        updated["controlRevision"] = Int64(s["controlRevision"] + 1)
        updated["updatedAt"] = utcnow()
        result = db().surveys.replace_one({"_id": s["_id"], "ownerId": g.user["_id"],
                                          "controlRevision": s["controlRevision"],
                                          "definitionVersion": s["definitionVersion"]}, updated, session=session)
        if result.matched_count != 1:
            fail("VERSION_CONFLICT", "问卷已被修改，请重新加载", 409)
        if current_app.extensions['database'].schema_stage == 2:
            sync_usages(updated, session)
        return updated
    if session is not None:
        return operation(session)
    return current_app.extensions["database"].transaction(operation)


@management.post("/surveys")
@authenticated
def create():
    data = body(("title", "description", "anonymous", "allowRepeat", "deadlineAt"), ("title",))
    now = utcnow()
    s = {"_id": ObjectId(), "schemaVersion": 1, "ownerId": g.user["_id"],
         "publicCode": new_id("share"), "state": "DRAFT", "definitionVersion": 1,
         "controlRevision": Int64(0), "publishedVersion": None,
         "deadlineAt": deadline(data.get("deadlineAt")),
         "draft": {"title": text_field(data["title"], "问卷标题", 200, 1),
                   "description": text_field(data.get("description", ""), "说明", 4000),
                   "policy": {"anonymous": boolean(data.get("anonymous", False), "anonymous"),
                              "allowRepeat": boolean(data.get("allowRepeat", False), "allowRepeat")}, "questions": []},
         "writeFence": Int64(0), "lastGateAt": None, "createdAt": now, "updatedAt": now}
    db().surveys.insert_one(s)
    return reply(survey_view(s), 201, survey_etag(s))


@management.get("/surveys")
@authenticated
def listing():
    limit, status = page_limit(), request.args.get("status")
    if status and status not in {"DRAFT", "PUBLISHED", "CLOSED", "EXPIRED"}:
        fail("STATUS_INVALID", "问卷状态无效", 400)
    scope = {"kind": "surveys", "owner": str(g.user["_id"]), "status": status}
    cursors = current_app.extensions["cursors"]
    position = cursors.decode(request.args.get("cursor"), scope)
    query = {"ownerId": g.user["_id"]}
    now = utcnow()
    if status == "EXPIRED":
        query.update(state="PUBLISHED", deadlineAt={"$ne": None, "$lte": now})
    elif status == "PUBLISHED":
        query.update(state="PUBLISHED", **{"$or": [{"deadlineAt": None}, {"deadlineAt": {"$gt": now}}]})
    elif status:
        query["state"] = status
    if position:
        query = {"$and": [query, {"$or": [{"updatedAt": {"$lt": deadline(position["at"])}},
                  {"updatedAt": deadline(position["at"]), "_id": {"$lt": oid(position["id"])}}]}]}
    rows = list(db().surveys.find(query).sort([("updatedAt", -1), ("_id", -1)]).limit(limit + 1))
    more, rows = len(rows) > limit, rows[:limit]
    items = []
    for s in rows:
        view = survey_view(s)
        view["title"] = s["draft"]["title"]
        del view["draft"]
        items.append(view)
    return reply({"items": items, "nextCursor": cursors.encode(scope, {"at": rows[-1]["updatedAt"], "id": rows[-1]["_id"]}) if more else None})


@management.get("/surveys/<survey_id>")
@authenticated
def detail(survey_id):
    s = owned(survey_id)
    return reply(survey_view(s), etag=survey_etag(s))


@management.patch("/surveys/<survey_id>")
@authenticated
def edit(survey_id):
    data = body(("title", "description", "anonymous", "allowRepeat", "deadlineAt"))
    def change(s, session):
        for key, maximum, minimum in (("title", 200, 1), ("description", 4000, 0)):
            if key in data:
                s["draft"][key] = text_field(data[key], key, maximum, minimum)
        for key in ("anonymous", "allowRepeat"):
            if key in data:
                s["draft"]["policy"][key] = boolean(data[key], key)
        if "deadlineAt" in data:
            s["deadlineAt"] = deadline(data["deadlineAt"])
        return s, bool(set(data) - {"deadlineAt"})
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))


@management.post("/surveys/<survey_id>/publish")
@authenticated
def publish(survey_id):
    body(())
    def change(s, session):
        validate_definition(s["draft"], publishing=True)
        # Database time agrees with the filling gate, independent of browser clocks.
        now = next(db().surveys.aggregate([{"$match": {"_id": s["_id"]}}, {"$project": {"now": "$$NOW"}}], session=session))["now"]
        if s["deadlineAt"] and s["deadlineAt"] <= now:
            fail("DEADLINE_PASSED", "请先延后或取消截止时间再发布", 409)
        current = db().survey_versions.find_one({"surveyId": s["_id"], "publishedVersion": s["publishedVersion"]}, session=session)
        if not current or current["sourceDefinitionVersion"] != s["definitionVersion"]:
            version = (s["publishedVersion"] or 0) + 1
            from .phase2_core import check_binding, sync_usages
            for q in s['draft']['questions']:
                check_binding(q, session)
            db().survey_versions.insert_one({"schemaVersion": 2 if any(q.get('reuseBinding') for q in s['draft']['questions']) else 1, "surveyId": s["_id"], "publishedVersion": version,
                                             "sourceDefinitionVersion": s["definitionVersion"], "definition": deepcopy(s["draft"]),
                                             "publishedAt": now}, session=session)
            s["publishedVersion"] = version
            if current_app.extensions['database'].schema_stage == 2:
                sync_usages(s, session, version)
        s["state"] = "PUBLISHED"
        return s, False
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))


@management.post("/surveys/<survey_id>/close")
@authenticated
def close(survey_id):
    body(())
    def change(s, session):
        if s["state"] == "DRAFT":
            fail("NOT_PUBLISHED", "草稿尚未发布", 409)
        s["state"] = "CLOSED"
        return s, False
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))


def find_question(s, qid):
    q = next((q for q in s["draft"]["questions"] if q["questionId"] == qid), None)
    if not q:
        fail("NOT_FOUND", "题目不存在", 404)
    return q


@management.post("/surveys/<survey_id>/questions")
@authenticated
def add_question(survey_id):
    data = body(("type", "title", "required", "options", "validation"), ("type", "title"))
    def change(s, session):
        s["draft"]["questions"].append(question(data))
        return s, True
    s = mutation(survey_id, change)
    return reply(survey_view(s), 201, survey_etag(s))


@management.patch("/surveys/<survey_id>/questions/<qid>")
@authenticated
def edit_question(survey_id, qid):
    data = body(("type", "title", "required", "options", "validation"))
    def change(s, session):
        old = find_question(s, qid)
        s["draft"]["questions"][s["draft"]["questions"].index(old)] = question(data, old)
        return s, True
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))


@management.delete("/surveys/<survey_id>/questions/<qid>")
@authenticated
def delete_question(survey_id, qid):
    def change(s, session):
        q = find_question(s, qid)
        for source in s["draft"]["questions"]:
            if any(r["target"] == qid for r in source["jumpRules"]):
                fail("QUESTION_REFERENCED", "请先修改指向该题的跳转规则", 409, [{"questionId": source["questionId"]}])
        s["draft"]["questions"].remove(q)
        return s, True
    s = mutation(survey_id, change)
    return reply(status=204, etag=survey_etag(s))


@management.put("/surveys/<survey_id>/question-order")
@authenticated
def order(survey_id):
    data = body(("questionIds",), ("questionIds",))["questionIds"]
    def change(s, session):
        questions = {q["questionId"]: q for q in s["draft"]["questions"]}
        if (not isinstance(data, list) or any(not isinstance(v, str) for v in data) or
                len(data) != len(set(data)) or set(data) != set(questions)):
            fail("ORDER_INVALID", "请提交全部题目标识，且不能重复")
        s["draft"]["questions"] = [questions[qid] for qid in data]
        return s, True
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))


@management.put("/surveys/<survey_id>/questions/<qid>/jump-rules")
@authenticated
def rules(survey_id, qid):
    data = body(("rules",), ("rules",))["rules"]
    def change(s, session):
        q = find_question(s, qid)
        q["jumpRules"] = jump_rules(q, data)
        return s, True
    s = mutation(survey_id, change)
    return reply(survey_view(s), etag=survey_etag(s))
