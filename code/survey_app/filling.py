"""Draft persistence and atomic, idempotent response acceptance."""
from bson import ObjectId
from bson.int64 import Int64
from flask import Blueprint, current_app, g, request
from pymongo import ReturnDocument

from .common import (attempt_etag, body, digest, fail, fingerprint, oid, reply,
                     require_match)
from .domain import canonical_answers
from .security import authenticated, db, rate_limit

filling = Blueprint("filling", __name__, url_prefix="/api/v1")


def execution_definition(definition):
    """Do not serialize resource identity, provenance or permissions to respondents."""
    return {'title': definition['title'], 'description': definition['description'], 'policy': definition['policy'],
            'questions': [{k: q[k] for k in ('questionId', 'type', 'title', 'required', 'options', 'validation', 'jumpRules') if k in q}
                          for q in definition['questions']]}


def version(survey_id, published_version, session):
    item = db().survey_versions.find_one({"surveyId": survey_id, "publishedVersion": published_version}, session=session)
    if not item:
        fail("VERSION_MISSING", "填写版本不存在，请联系问卷创建者", 409)
    return item["definition"]


def gate(survey_id, session):
    # This MUST be a real write on the same document as close/publish/deadline edits.
    survey = db().surveys.find_one_and_update({"_id": survey_id, "state": "PUBLISHED", "$expr": {
        "$or": [{"$eq": ["$deadlineAt", None]}, {"$gt": ["$deadlineAt", "$$NOW"]}]}},
        {"$inc": {"writeFence": Int64(1)}, "$currentDate": {"lastGateAt": True}},
        return_document=ReturnDocument.AFTER, session=session)
    if not survey:
        fail("NOT_COLLECTING", "问卷尚未发布、已关闭或已截止", 409)
    return survey


def repeat_check(survey, user_id, session):
    active = version(survey["_id"], survey["publishedVersion"], session)
    guard = db().participation_guards.find_one({"surveyId": survey["_id"], "userId": user_id}, session=session)
    if not active["policy"]["allowRepeat"] and guard and guard["acceptedCount"] > 0:
        fail("ALREADY_SUBMITTED", "你已提交过本问卷，当前设置不允许重复填写", 409)


def my_attempt(attempt_id, session):
    attempt = db().attempts.find_one({"_id": oid(attempt_id), "userId": g.user["_id"]}, session=session)
    if not attempt:
        fail("NOT_FOUND", "填写记录不存在或无权访问", 404)
    return attempt


def attempt_view(a, definition=None):
    result = {"attemptId": a["_id"], "surveyId": a["surveyId"], "publishedVersion": a["publishedVersion"],
              "anonymous": a["anonymous"], "revision": a["revision"], "status": a["state"],
              "answers": a["answers"], "visitedQuestionIds": a["visitedQuestionIds"],
              "nextQuestionId": a["nextQuestionId"], "responseId": a["responseId"], "submittedAt": a["submittedAt"],
              "updatedAt": a["updatedAt"]}
    if definition is not None:
        result["definition"] = execution_definition(definition)
    return result


@filling.get("/public-surveys/<public_code>")
@authenticated
def public(public_code):
    def operation(session):
        s = db().surveys.find_one({"publicCode": public_code}, session=session)
        if not s:
            fail("NOT_FOUND", "问卷不存在", 404)
        now = next(db().surveys.aggregate([{"$match": {"_id": s["_id"]}}, {"$project": {"now": "$$NOW"}}], session=session))["now"]
        if s["state"] != "PUBLISHED" or s["deadlineAt"] and s["deadlineAt"] <= now:
            fail("NOT_COLLECTING", "问卷尚未发布、已关闭或已截止", 409)
        definition = version(s["_id"], s["publishedVersion"], session)
        guard = db().participation_guards.find_one({"surveyId": s["_id"], "userId": g.user["_id"]}, session=session)
        latest_attempt = db().attempts.find_one({"surveyId": s["_id"], "userId": g.user["_id"], "state": "IN_PROGRESS"},
                                               sort=[("updatedAt", -1), ("_id", -1)], session=session)
        accepted = bool(guard and guard["acceptedCount"])
        return {"publicCode": s["publicCode"], "publishedVersion": s["publishedVersion"],
                "definition": execution_definition(definition), "deadlineAt": s["deadlineAt"], "serverNow": now,
                "hasSubmitted": accepted, "canStart": not accepted or definition["policy"]["allowRepeat"],
                "resumeAttemptId": latest_attempt["_id"] if latest_attempt else None}
    return reply(current_app.extensions["database"].transaction(operation))


@filling.post("/public-surveys/<public_code>/attempts")
@authenticated
def start(public_code):
    body(())
    rate_limit("start", 60)
    def operation(session):
        found = db().surveys.find_one({"publicCode": public_code}, {"_id": 1}, session=session)
        if not found:
            fail("NOT_FOUND", "问卷不存在", 404)
        s = gate(found["_id"], session)
        repeat_check(s, g.user["_id"], session)
        definition = version(s["_id"], s["publishedVersion"], session)
        first = definition["questions"][0]["questionId"]
        now = s["lastGateAt"]
        a = {"_id": ObjectId(), "schemaVersion": 1, "surveyId": s["_id"], "userId": g.user["_id"],
             "publishedVersion": s["publishedVersion"], "anonymous": definition["policy"]["anonymous"],
             "revision": Int64(0), "state": "IN_PROGRESS", "answers": [], "visitedQuestionIds": [],
             "nextQuestionId": first, "responseId": None, "submittedAt": None, "createdAt": now, "updatedAt": now}
        db().attempts.insert_one(a, session=session)
        return a, definition
    a, definition = current_app.extensions["database"].transaction(operation)
    return reply(attempt_view(a, definition), 201, attempt_etag(a))


@filling.get("/attempts/<attempt_id>")
@authenticated
def get_attempt(attempt_id):
    def operation(session):
        a = my_attempt(attempt_id, session)
        return a, version(a["surveyId"], a["publishedVersion"], session)
    a, definition = current_app.extensions["database"].transaction(operation)
    return reply(attempt_view(a, definition), etag=attempt_etag(a))


@filling.put("/attempts/<attempt_id>/answers")
@authenticated
def save(attempt_id):
    entries = body(("answers",), ("answers",))["answers"]
    supplied = require_match(r'"attempt-\d+"')
    rate_limit("save", 180)
    def operation(session):
        a = my_attempt(attempt_id, session)
        if a["state"] != "IN_PROGRESS":
            fail("ATTEMPT_SUBMITTED", "这次填写已提交，请新建填写", 409)
        if supplied != attempt_etag(a):
            fail("VERSION_CONFLICT", "答案已被其他操作修改，请重新加载", 409)
        s = gate(a["surveyId"], session)
        definition = version(a["surveyId"], a["publishedVersion"], session)
        canonical = canonical_answers(definition, entries)
        updated = {k: v for k, v in canonical.items() if k != "removedQuestionIds"}
        updated.update(revision=Int64(a["revision"] + 1), updatedAt=s["lastGateAt"])
        result = db().attempts.update_one({"_id": a["_id"], "userId": g.user["_id"],
                                          "revision": a["revision"], "state": "IN_PROGRESS"}, {"$set": updated}, session=session)
        if not result.matched_count:
            fail("VERSION_CONFLICT", "答案已被修改，请重新加载", 409)
        a.update(updated)
        return a, canonical["removedQuestionIds"]
    a, removed = current_app.extensions["database"].transaction(operation)
    result = attempt_view(a)
    result["removedQuestionIds"] = removed
    return reply(result, etag=attempt_etag(a))


@filling.post("/attempts/<attempt_id>/submit")
@authenticated
def submit(attempt_id):
    body(())
    supplied = require_match(r'"attempt-\d+"')
    key = request.headers.get("Idempotency-Key", "")
    if not 16 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        fail("IDEMPOTENCY_REQUIRED", "Idempotency-Key 应为 16～128 位可打印 ASCII 字符", 400)
    attempt_oid = oid(attempt_id)
    key_hash = digest(key)
    fp = fingerprint({"userId": str(g.user["_id"]), "attemptId": str(attempt_oid), "ifMatch": supplied})
    rate_limit("submit", 60)
    def operation(session):
        receipt = db().submission_keys.find_one({"userId": g.user["_id"], "keyHash": key_hash}, session=session)
        if receipt:
            if receipt["requestFingerprint"] != fp:
                fail("IDEMPOTENCY_CONFLICT", "该幂等键已用于其他请求", 409)
            response = db().responses.find_one({"_id": receipt["responseId"]}, session=session)
            return {"responseId": response["_id"], "submittedAt": response["acceptedAt"]}, True
        a = my_attempt(attempt_id, session)
        if a["state"] != "IN_PROGRESS":
            fail("ATTEMPT_SUBMITTED", "这次填写已提交，请勿换键重提；重复填写应新建填写", 409)
        if supplied != attempt_etag(a):
            fail("VERSION_CONFLICT", "请先保存并使用最新答案版本提交", 409)
        s = gate(a["surveyId"], session)
        repeat_check(s, g.user["_id"], session)
        definition = version(a["surveyId"], a["publishedVersion"], session)
        from .common import plain
        canonical = canonical_answers(definition, plain(a["answers"]), complete=True)
        now = s["lastGateAt"]
        guard = db().participation_guards.find_one_and_update(
            {"surveyId": a["surveyId"], "userId": g.user["_id"]},
            {"$setOnInsert": {"schemaVersion": 1}, "$inc": {"acceptedCount": Int64(1)},
             "$set": {"updatedAt": now}}, upsert=True, return_document=ReturnDocument.AFTER, session=session)
        response_id = ObjectId()
        response = {"_id": response_id, "schemaVersion": 2, "surveyId": a["surveyId"], "attemptId": a["_id"],
                    "participationId": guard["_id"], "submissionSequence": Int64(guard["acceptedCount"]),
                    "publishedVersion": a["publishedVersion"], "anonymous": a["anonymous"],
                    "answers": canonical["answers"], "visitedQuestionIds": canonical["visitedQuestionIds"], "acceptedAt": now}
        if not a["anonymous"]:
            response["respondentUserId"] = g.user["_id"]
        db().responses.insert_one(response, session=session)
        result = db().attempts.update_one({"_id": a["_id"], "userId": g.user["_id"], "revision": a["revision"], "state": "IN_PROGRESS"},
            {"$set": {"state": "SUBMITTED", "responseId": response_id, "submittedAt": now, "updatedAt": now,
                      "answers": canonical["answers"], "visitedQuestionIds": canonical["visitedQuestionIds"], "nextQuestionId": None},
             "$inc": {"revision": Int64(1)}}, session=session)
        if not result.matched_count:
            fail("VERSION_CONFLICT", "填写记录已变更，请重新加载", 409)
        db().submission_keys.insert_one({"schemaVersion": 1, "userId": g.user["_id"], "keyHash": key_hash,
            "requestFingerprint": fp, "attemptId": a["_id"], "responseId": response_id,
            "submittedIfMatch": supplied, "createdAt": now}, session=session)
        return {"responseId": response_id, "submittedAt": now}, False
    result, replay = current_app.extensions["database"].transaction(operation)
    result["replayed"] = replay
    return reply(result, 200 if replay else 201)
