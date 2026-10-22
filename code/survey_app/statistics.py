"""Deduplicate whole responses BEFORE any version/question filter or pagination."""
from decimal import Decimal, ROUND_HALF_UP, localcontext

from flask import Blueprint, current_app, g, request

from .common import deadline, fail, oid, page_limit, reply, utcnow
from .domain import as_decimal, empty
from .management import owned
from .security import authenticated, db

statistics = Blueprint("statistics", __name__, url_prefix="/api/v1")


def latest_prefix(survey_id):
    return [{"$match": {"surveyId": survey_id}},
            {"$sort": {"participationId": 1, "submissionSequence": -1}},
            {"$group": {"_id": "$participationId", "latest": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$latest"}}]


def version_arg():
    value = request.args.get("publishedVersion")
    if value is None:
        return None
    try:
        result = int(value)
        if not 1 <= result <= 2147483647:
            raise ValueError()
        return result
    except ValueError:
        fail("VERSION_INVALID", "publishedVersion 应为正整数", 400)


def aggregate(pipeline, session):
    return db().responses.aggregate(pipeline, session=session, allowDiskUse=True, maxTimeMS=10000)


def average(total, count):
    if not count:
        return None
    value = (total / Decimal(count)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if value == 0:
        return "0"
    return format(value, "f").rstrip("0").rstrip(".")


def metrics(survey_id, qid=None):
    selected = version_arg()
    cursors = current_app.extensions["cursors"]
    scope = {"kind": "stat_versions", "owner": str(g.user["_id"]), "survey": survey_id, "question": qid}
    before = cursors.decode(request.args.get("versionCursor"), scope)
    if selected and before:
        fail("PAGE_INVALID", "指定版本时不应携带版本分页游标", 400)
    def operation(session):
        s = owned(survey_id, session)
        query = {"surveyId": s["_id"]}
        if selected:
            query["publishedVersion"] = selected
        elif before:
            query["publishedVersion"] = {"$lt": int(before)}
        if qid:
            query["definition.questions.questionId"] = qid
        versions = list(db().survey_versions.find(query, session=session).sort("publishedVersion", -1).limit(21))
        if selected and not versions:
            fail("NOT_FOUND", "该发布版本或题目不存在", 404)
        if qid and not versions and not db().survey_versions.find_one({"surveyId": s["_id"], "definition.questions.questionId": qid}, {"_id": 1}, session=session):
            fail("NOT_FOUND", "该题目尚无发布版本", 404)
        has_more, versions = len(versions) > 20, versions[:20]
        count_pipeline = latest_prefix(s["_id"])
        if selected:
            count_pipeline.append({"$match": {"publishedVersion": selected}})
        counts = {v["_id"]: v["people"] for v in aggregate(count_pipeline + [{"$group": {"_id": "$publishedVersion", "people": {"$sum": 1}}}], session)}
        result = {}
        sums = {}
        for v in versions:
            number = v["publishedVersion"]
            qs = [q for q in v["definition"]["questions"] if not qid or q["questionId"] == qid]
            question_stats = {}
            for q in qs:
                row = {"questionId": q["questionId"], "type": q["type"], "title": q["title"],
                       "reachedCount": 0, "answeredCount": 0}
                if q["type"] in ("SINGLE", "MULTIPLE"):
                    row["options"] = [{"optionId": o["optionId"], "label": o["label"], "count": 0} for o in q["options"]]
                if q["type"] == "NUMBER":
                    row["average"] = None
                    sums[(number, q["questionId"])] = Decimal(0)
                question_stats[q["questionId"]] = row
            result[number] = {"publishedVersion": number, "title": v["definition"]["title"],
                              "submittedCount": counts.get(number, 0), "questions": question_stats}
        if result:
            pipeline = latest_prefix(s["_id"]) + [{"$match": {"publishedVersion": {"$in": list(result)}}},
                {"$project": {"_id": 0, "publishedVersion": 1, "answers": 1, "visitedQuestionIds": 1}}]
            # Stream whitelisted fields; never build a giant $facet/$push answer array.
            # Extra precision covers the entire Decimal128 exponent range when summing,
            # avoiding Decimal128 aggregation overflow and binary floating point conversion.
            with localcontext() as context:
                context.prec = 12500
                for response in aggregate(pipeline, session):
                    number = response["publishedVersion"]
                    stats = result[number]["questions"]
                    for visited in response["visitedQuestionIds"]:
                        if visited in stats:
                            stats[visited]["reachedCount"] += 1
                    for answer in response["answers"]:
                        key = answer["questionId"]
                        if key not in stats or empty(answer["value"]):
                            continue
                        row, value = stats[key], answer["value"]
                        row["answeredCount"] += 1
                        if row["type"] in ("SINGLE", "MULTIPLE"):
                            values = {value} if row["type"] == "SINGLE" else set(value)
                            for option in row["options"]:
                                option["count"] += int(option["optionId"] in values)
                        if row["type"] == "NUMBER":
                            sums[(number, key)] += as_decimal(value)
                for (number, key), total in sums.items():
                    row = result[number]["questions"][key]
                    row["average"] = average(total, row["answeredCount"])
        for item in result.values():
            item["questions"] = list(item["questions"].values())
        return {"submittedCount": sum(counts.values()), "versions": list(result.values()), "generatedAt": utcnow(),
                "nextVersionCursor": cursors.encode(scope, versions[-1]["publishedVersion"]) if has_more else None}
    return current_app.extensions["database"].transaction(operation)


@statistics.get("/surveys/<survey_id>/statistics")
@authenticated
def overview(survey_id):
    return reply(metrics(survey_id))


@statistics.get("/surveys/<survey_id>/questions/<qid>/statistics")
@authenticated
def question_statistics(survey_id, qid):
    return reply(metrics(survey_id, qid))


@statistics.get("/surveys/<survey_id>/questions/<qid>/answers")
@authenticated
def answers(survey_id, qid):
    selected, limit = version_arg(), page_limit()
    cursors = current_app.extensions["cursors"]
    scope = {"kind": "answers", "owner": str(g.user["_id"]), "survey": survey_id, "question": qid, "version": selected}
    position = cursors.decode(request.args.get("cursor"), scope)
    def operation(session):
        s = owned(survey_id, session)
        definition_query = {"surveyId": s["_id"], "definition.questions": {"$elemMatch": {"questionId": qid, "type": {"$in": ["TEXT", "NUMBER"]}}}}
        if selected:
            definition_query["publishedVersion"] = selected
        if not db().survey_versions.find_one(definition_query, {"_id": 1}, session=session):
            fail("NOT_FOUND", "该发布版本的填空题不存在", 404)
        pipeline = latest_prefix(s["_id"])
        if selected:
            pipeline.append({"$match": {"publishedVersion": selected}})
        pipeline += [{"$unwind": "$answers"}, {"$match": {"answers.questionId": qid,
                      "answers.type": {"$in": ["TEXT", "NUMBER"]}, "answers.value": {"$nin": [None, ""]}}}]
        if position:
            at = deadline(position["at"])
            pipeline.append({"$match": {"$or": [{"acceptedAt": {"$lt": at}}, {"acceptedAt": at, "_id": {"$lt": oid(position["id"])}}]}})
        pipeline += [{"$sort": {"acceptedAt": -1, "_id": -1}}, {"$limit": limit + 1},
                     {"$project": {"_id": 1, "publishedVersion": 1, "acceptedAt": 1, "value": "$answers.value",
                                    "anonymous": 1, "respondentUserId": 1}}]
        rows = list(aggregate(pipeline, session))
        more, rows = len(rows) > limit, rows[:limit]
        items = []
        for row in rows:
            # Explicit output allowlist. Encrypted cursor is the only carrier of a private seek key.
            item = {"publishedVersion": row["publishedVersion"], "value": row["value"],
                    "submittedAt": row["acceptedAt"], "anonymous": row["anonymous"]}
            if not row["anonymous"]:
                user = db().users.find_one({"_id": row["respondentUserId"]}, {"username": 1}, session=session)
                item["respondent"] = {"username": user["username"] if user else "账号已不可用"}
            items.append(item)
        return {"items": items, "nextCursor": cursors.encode(scope, {"at": rows[-1]["acceptedAt"], "id": rows[-1]["_id"]}) if more else None}
    return reply(current_app.extensions["database"].transaction(operation))
