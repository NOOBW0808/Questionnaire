import copy
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from conftest import CASES
from support import Survey, data, error, default_questions, rule


@pytest.mark.parametrize("case_id", [f"TC01-A{i:02}" for i in range(1, 13)])
def test_creation(ctx, case_id):
    n = int(case_id[-2:])
    owner = ctx.owner
    before = ctx.db.surveys.count_documents({})
    if 3 <= n <= 10:
        payload = CASES[case_id]["input"]
        error(owner.call("POST", "/surveys", payload), 422)
        assert ctx.db.surveys.count_documents({}) == before
        return
    if n == 12:
        error(owner.call("POST", "/surveys", raw='{"title":'), 400, "BAD_JSON")
        assert ctx.db.surveys.count_documents({}) == before
        return
    payload = CASES[case_id]["input"]
    if n == 1:
        payload = payload["POST /surveys"]
    s = Survey(ctx, owner, payload)
    persisted = ctx.db.surveys.find_one({"_id": ObjectId(s.id)})
    ctx.evidence("创建持久化（不含会话或密码）", persisted)
    assert persisted["ownerId"] == ObjectId(owner.uid)
    assert s.view["state"] == "DRAFT" and s.view["publishedVersion"] is None
    assert ctx.db.survey_versions.count_documents({"surveyId": ObjectId(s.id)}) == 0
    assert ctx.db.responses.count_documents({"surveyId": ObjectId(s.id)}) == 0
    assert s.public(expected=409)["code"] == "NOT_COLLECTING"
    original = copy.deepcopy(s.view)
    assert s.refresh() == original
    if n == 1:
        assert s.view["draft"] == {"title": "课程体验调查", "description": "",
            "policy": {"anonymous": False, "allowRepeat": False}, "questions": []}
        assert s.view["definitionVersion"] == 1 and s.view["controlRevision"] == 0
    elif n == 2:
        assert s.view["draft"]["title"] == "中文问卷🌟"
        assert s.view["draft"]["description"] == "第一行\n第二行"
        assert persisted["deadlineAt"] == datetime(2099, 12, 31, 15, 59, tzinfo=timezone.utc)
        other = Survey(ctx, owner, {"title": "另一问卷"})
        other.patch({"title": "独立更新"})
        owner.login(owner.username)
        assert s.refresh() == original
    elif n == 11:
        assert len(s.view["draft"]["title"]) == 200
        assert len(s.view["draft"]["description"]) == 4000


@pytest.mark.parametrize("case_id", [f"TC02-A{i:02}" for i in range(1, 18)])
def test_questions(ctx, case_id):
    n = int(case_id[-2:])
    s = ctx.form(questions=[], publish=False) if n <= 14 else ctx.form(publish=False)
    if n <= 4:
        q = CASES[case_id]["input"]
        old = copy.deepcopy(s.view)
        s.add(q)
        result = copy.deepcopy(s.qs[0])
        assert result["type"] == q["type"] and result["title"] == q["title"] and result["required"]
        assert result["validation"] == q["validation"]
        assert ("options" in result) == (n <= 2)
        assert s.view["definitionVersion"] == old["definitionVersion"] + 1
        s.refresh()
        assert s.qs == [result]
        return
    if 6 <= n <= 11:
        old = copy.deepcopy(s.view)
        s.add(CASES[case_id]["input"], expected=422)
        assert s.refresh() == old
        return
    if n == 5:
        s.add(default_questions()[0])
        s.publish()
        old = copy.deepcopy(s.qs[0])
        snapshot = ctx.db.survey_versions.find_one({"surveyId": ObjectId(s.id)})
        options = copy.deepcopy(old["options"])
        options[0]["label"] = "继续参加"
        s.edit(0, {"title": "更新题干", "options": options[::-1]})
        assert s.qs[0]["questionId"] == old["questionId"]
        assert s.qs[0]["options"] == options[::-1]
        assert ctx.db.survey_versions.find_one({"_id": snapshot["_id"]}) == snapshot
        s.change("DELETE", "/questions/" + old["questionId"], expected=204)
        s.add(default_questions()[0])
        assert s.qs[0]["questionId"] != old["questionId"]
        assert not {o["optionId"] for o in old["options"]} & {o["optionId"] for o in s.qs[0]["options"]}
    elif n == 12:
        for i in range(200):
            s.add({"type": "TEXT", "title": f"题目{i}"})
        assert len(s.qs) == 200
        snapshot = copy.deepcopy(s.view)
        s.add({"type": "TEXT", "title": "第201题"}, expected=422)
        assert s.refresh() == snapshot
        s.publish()
    elif n == 13:
        s.add({"type": "SINGLE", "title": "100项", "options": [{"label": str(i)} for i in range(100)]})
        assert len(s.qs[0]["options"]) == 100
        old = copy.deepcopy(s.view)
        s.add({"type": "SINGLE", "title": "101项", "options": [{"label": str(i)} for i in range(101)]}, expected=422)
        assert s.refresh() == old
    elif n == 14:
        s.add({"type": "SINGLE", "title": "题", "options": [{"label": "中" * 200}]})
        assert len(s.qs[0]["options"][0]["label"]) == 200
        s.add({"type": "SINGLE", "title": "题", "options": [{"label": "中" * 201}]}, expected=422)
        assert len(s.qs) == 1
    elif n == 15:
        ids = [q["questionId"] for q in s.qs]
        order = [ids[3], *ids[:3]]
        s.change("PUT", "/question-order", {"questionIds": order})
        assert [q["questionId"] for q in s.qs] == order
        for invalid in [ids[:2], [ids[0], ids[0], *ids[2:]], [*ids[:3], "unknown"]]:
            s.change("PUT", "/question-order", {"questionIds": invalid}, expected=422)
        s.edit(0, {"type": "TEXT"}, expected=422)
        s.publish()
        a = s.attempt()
        assert a.view["nextQuestionId"] == ids[3]
    elif n == 16:
        q1, q4 = s.qs[0], s.qs[3]
        s.rule(0, [rule("EQ", q1["options"][1]["optionId"], q4["questionId"])])
        old = copy.deepcopy(s.view)
        s.change("DELETE", "/questions/" + q4["questionId"], expected=409, code="QUESTION_REFERENCED")
        s.edit(0, {"options": [q1["options"][0]]}, expected=409, code="OPTION_REFERENCED")
        assert s.refresh() == old
        s.rule(0, [])
        s.change("DELETE", "/questions/" + q4["questionId"], expected=204)
    elif n == 17:
        old = copy.deepcopy(s.view)
        s.patch({"title": "missing"}, expected=428, headers={"If-Match": None})
        assert s.refresh() == old
        stale = s.etag
        s.patch({"title": "版本一"})
        current = copy.deepcopy(s.view)
        s.patch({"title": "版本二"}, expected=409, headers={"If-Match": stale})
        assert s.refresh() == current


@pytest.mark.parametrize("case_id", [f"TC03-A{i:02}" for i in range(1, 19)])
def test_navigation(ctx, case_id):
    n = int(case_id[-2:])
    s = ctx.form(publish=False)
    q = s.qs
    if n in (1, 18):
        s.rule(0, [rule("EQ", q[0]["options"][1]["optionId"])])
        s.publish()
        a = s.attempt()
        if n == 18:
            a.save()
            entries = a.entries()
            entries[0]["value"] = q[0]["options"][1]["optionId"]
            result = data(a.save(entries=entries))
            assert set(result["removedQuestionIds"]) == {x["questionId"] for x in q[1:]}
            for field, value in [("nextQuestionId", "END"), ("visitedQuestionIds", [])]:
                error(a.client.call("PUT", a.path + "/answers", {"answers": entries, field: value}, headers={"If-Match": a.etag}), 422)
        else:
            a.save(values=[q[0]["options"][1]["optionId"]])
            other = s.attempt()
            other.save(values=[q[0]["options"][0]["optionId"]])
            assert other.view["nextQuestionId"] == q[1]["questionId"]
        assert a.view["nextQuestionId"] is None
        assert a.view["visitedQuestionIds"] == [q[0]["questionId"]]
        a.submit()
        persisted = ctx.db.responses.find_one({"attemptId": ObjectId(a.id)})
        assert [x["questionId"] for x in persisted["answers"]] == [q[0]["questionId"]]
    elif n in (2, 3, 4):
        op = ["ANY_OF", "ALL_OF", "EXACT_SET"][n - 2]
        opts = q[1]["options"]
        s.rule(1, [rule(op, [opts[0]["optionId"], opts[1]["optionId"]])])
        s.publish()
        mapping = {o["label"]: o["optionId"] for o in opts}
        for values, hit in CASES[case_id]["input"]["输入及命中"]:
            a = s.attempt()
            a.save(values=[q[0]["options"][0]["optionId"], [mapping[x] for x in values]])
            assert a.view["nextQuestionId"] == (None if hit else q[2]["questionId"])
            if hit:
                a.submit()
    elif 5 <= n <= 10:
        op = ["EQ", "GT", "GTE", "LT", "LTE", "BETWEEN"][n - 5]
        s.add({"type": "TEXT", "title": "Q5", "required": True})
        s.rule(3, [rule(op, CASES[case_id]["input"]["条件值"])])
        s.publish()
        for value, hit in CASES[case_id]["input"]["数字答案及命中"]:
            a = s.attempt()
            vals = [q[0]["options"][0]["optionId"], [q[1]["options"][0]["optionId"]], "很好", value]
            a.save(vals)
            assert a.view["nextQuestionId"] == (None if hit else s.qs[4]["questionId"])
    elif n == 11:
        s.edit(3, {"required": False})
        s.add({"type": "TEXT", "title": "Q5"})
        s.add({"type": "TEXT", "title": "Q6"})
        # Q5 target in original spec equals natural next; Q6 makes priority observable.
        s.rule(3, [rule("GTE", "10", s.qs[5]["questionId"], 20), rule("GTE", "18", "END", 10)])
        s.publish()
        assert [r["priority"] for r in s.qs[3]["jumpRules"]] == [10, 20]
        for value, target in [("20", None), ("12", s.qs[5]["questionId"]), ("5", s.qs[4]["questionId"]), (None, s.qs[4]["questionId"])]:
            a = s.attempt()
            a.save([q[0]["options"][0]["optionId"], [q[1]["options"][0]["optionId"]], "很好", value])
            assert a.view["nextQuestionId"] == target
    elif 12 <= n <= 16:
        value = q[0]["options"][1]["optionId"]
        rules = [rule("EQ", value)]
        if n == 12:
            rules.append(rule("EQ", q[0]["options"][0]["optionId"], q[3]["questionId"]))
        elif n == 13:
            rules[0]["target"] = "unknown"
        elif n == 14:
            rules[0]["target"] = q[0]["questionId"]
        elif n == 15:
            rules[0]["value"] = "unknown"
        else:
            rules[0]["operator"] = "SCRIPT"
        old = copy.deepcopy(s.view)
        s.rule(0, rules, expected=422)
        assert s.refresh() == old
    elif n == 17:
        old = copy.deepcopy(s.view)
        s.rule(3, [rule("GT", "18", q[0]["questionId"])], expected=422)
        s.rule(2, [rule("EQ", "text")], expected=422)
        s.rule(3, [rule("BETWEEN", ["20", "18"])], expected=422)
        s.rule(0, [rule("EQ", q[0]["options"][0]["optionId"], priority=i) for i in range(101)], expected=422)
        assert s.refresh() == old
        s.rule(0, [rule("EQ", q[0]["options"][0]["optionId"], priority=i) for i in range(100)])
        assert len(s.qs[0]["jumpRules"]) == 100
        s.change("PUT", "/question-order", {"questionIds": [q[3]["questionId"], *[x["questionId"] for x in q[:3]]]})


@pytest.mark.parametrize("case_id", [f"TC04-A{i:02}" for i in range(1, 29)])
def test_validation(ctx, case_id):
    n = int(case_id[-2:])
    if n <= 5:
        s = ctx.form(publish=False)
        spec = CASES[case_id]["input"]
        i = {"MULTIPLE": 1, "TEXT": 2, "NUMBER": 3}[spec["题型"]]
        old = copy.deepcopy(s.view)
        s.edit(i, {"validation": spec["validation"]}, expected=422)
        assert s.refresh() == old
        return
    if n == 6:
        for qs in [[], [{"type": "SINGLE", "title": "空"}],
            [{"type": "MULTIPLE", "title": "冲突", "options": [{"label": "a"}, {"label": "b"}], "validation": {"maxSelections": 3}}],
            [{"type": "NUMBER", "title": "无整数", "validation": {"minValue": "0.1", "maxValue": "0.9", "integerOnly": True}}],
            [{"type": "MULTIPLE", "title": "必答零", "required": True, "options": [{"label": "a"}], "validation": {"exactSelections": 0}}]]:
            s = ctx.form(questions=qs, publish=False)
            old = copy.deepcopy(s.view)
            s.publish(expected=422)
            assert s.refresh() == old
            assert ctx.db.survey_versions.count_documents({"surveyId": ObjectId(s.id)}) == 0
        return
    s = ctx.form(publish=False)
    if n == 16:
        s.edit(2, {"validation": {"maxLength": 10000}})
    if n == 28:
        s.edit(3, {"validation": {"minValue": "0", "maxValue": "120", "integerOnly": False}})
    s.publish()
    a = s.attempt()
    q = s.qs
    base = [q[0]["options"][0]["optionId"], [q[1]["options"][0]["optionId"]], "很好", "18"]
    if n == 7:
        for values, next_id in [([], q[0]["questionId"]), ([None], q[0]["questionId"]), (base[:1], q[1]["questionId"]),
                                ([base[0], [], "", None], q[1]["questionId"])]:
            a.save(values)
            assert a.view["nextQuestionId"] == next_id
            a.submit(expected=422, code="ANSWER_REQUIRED")
        for i in [1, 3]:
            s.edit(i, {"required": False})
        s.publish()
        b = s.attempt()
        b.save([base[0], [], "", None])
        assert b.view["nextQuestionId"] is None
        b.submit()
    elif 8 <= n <= 13:
        invalid = {8: (0, "unknown"), 9: (0, [base[0]]), 10: (1, base[1] * 2), 11: (1, ["unknown"]),
                   12: (1, base[1][0]), 13: (1, [x["optionId"] for x in q[1]["options"]])}
        i, value = invalid[n]
        values = copy.deepcopy(base)
        values[i] = value
        old = copy.deepcopy(a.view)
        err = error(a.save(values, expected=422), 422)
        assert err["details"][0]["questionId"] == q[i]["questionId"]
        assert a.refresh()["answers"] == old["answers"]
    elif n == 14:
        opts = [x["optionId"] for x in q[1]["options"]]
        for values, ok in [([opts[0]], True), (opts[:2], True), (opts, False)]:
            b = s.attempt()
            b.save([base[0], values, *base[2:]], expected=200 if ok else 422)
        s.edit(1, {"validation": {"minSelections": 1, "maxSelections": 2, "exactSelections": 2}})
        s.publish()
        for values, ok in [(opts[:1], False), (opts[1::-1], True)]:
            b = s.attempt()
            b.save([base[0], values, *base[2:]], expected=200 if ok else 422)
            if ok:
                assert b.view["answers"][1]["value"] == opts[:2]
    elif n == 15:
        for text, ok in [(" 好 ", False), ("很好", True), ("  很好  ", True), ("1234567890", True),
                         ("12345678901", False), ("😀好", True), ("   ", True), ("第一行\n第二行", True)]:
            a.save([*base[:2], text, base[3]], expected=200 if ok else 422)
            if ok:
                assert a.view["answers"][2]["value"] == text.strip()
    elif n == 16:
        a.save([*base[:2], "文" * 10000, base[3]])
        assert len(a.view["answers"][2]["value"]) == 10000
        for text in ["文" * 10001, "\ud800"]:
            a.save([*base[:2], text, base[3]], expected=422)
        assert len(a.refresh()["answers"][2]["value"]) == 10000
    elif 17 <= n <= 27:
        value = CASES[case_id]["input"]["Q4.value"]
        a.save([*base[:3], value], expected=200 if n <= 18 else 422)
        if n <= 18:
            assert a.view["answers"][3]["value"] == value
            a.submit()
            response = ctx.db.responses.find_one({"attemptId": ObjectId(a.id)})
            from bson.decimal128 import Decimal128
            assert isinstance(response["answers"][3]["value"], Decimal128)
        else:
            assert not a.refresh()["answers"]
    elif n == 28:
        a.save([*base[:3], "0.1"])
        assert a.view["answers"][3]["value"] == "0.1"
        entries = a.entries(base)
        variants = [entries + [entries[0]], [{"questionId": "unknown", "value": "x"}],
                    [*entries[:3], {**entries[3], "type": "TEXT"}], [entries[0]] * 201]
        for values in variants:
            a.save(entries=values, expected=422)
        assert a.refresh()["answers"][3]["value"] == "0.1"
