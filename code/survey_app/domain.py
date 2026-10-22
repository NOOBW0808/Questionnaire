"""Question configuration, exact decimals and deterministic forward navigation."""
import re
import secrets
from copy import deepcopy
from decimal import Decimal, DecimalException

from bson import BSON
from bson.decimal128 import Decimal128

from .common import boolean, fail, fields, text_field

TYPES = {"SINGLE", "MULTIPLE", "TEXT", "NUMBER"}
DECIMAL_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def new_id(prefix):
    return prefix + "_" + secrets.token_hex(12)


def decimal128(value):
    if not isinstance(value, str) or len(value) > 100 or not DECIMAL_PATTERN.fullmatch(value):
        fail("NUMBER_INVALID", "数字应使用有限十进制字符串，例如 18 或 2.5")
    try:
        number = Decimal(value)
        encoded = Decimal128(number)  # Traps inexact conversion and overflow.
        if not number.is_finite() or encoded.to_decimal() != number:
            raise ValueError()
        return encoded
    except (DecimalException, ValueError, OverflowError):
        fail("NUMBER_INVALID", "数字超出 Decimal128 的精确表示范围")


def as_decimal(value):
    return value.to_decimal() if isinstance(value, Decimal128) else value


def empty(value):
    return value is None or value == "" or value == []


def int_constraint(value, name, maximum=2147483647):
    if type(value) is not int or not 0 <= value <= maximum:
        fail("CONSTRAINT_INVALID", f"{name}必须为 0～{maximum} 的整数")
    return value


def constraints(kind, data):
    allowed = {"SINGLE": (), "MULTIPLE": ("minSelections", "maxSelections", "exactSelections"),
               "TEXT": ("minLength", "maxLength"), "NUMBER": ("minValue", "maxValue", "integerOnly")}[kind]
    fields(data, allowed)
    result = {}
    for key, value in data.items():
        if key in ("minValue", "maxValue"):
            result[key] = decimal128(value)
        elif key == "integerOnly":
            result[key] = boolean(value, key)
        else:
            result[key] = int_constraint(value, key)
    if kind == "NUMBER":
        result.setdefault("integerOnly", False)
    return result


def question(data, existing=None):
    fields(data, ("type", "title", "required", "options", "validation"), () if existing else ("type", "title"))
    q = deepcopy(existing) if existing else {"questionId": new_id("q"), "required": False,
                                            "validation": {}, "jumpRules": []}
    kind = data.get("type", q.get("type"))
    if not isinstance(kind, str) or kind not in TYPES:
        fail("QUESTION_TYPE", "题型应为 SINGLE、MULTIPLE、TEXT 或 NUMBER")
    if existing and kind != existing["type"]:
        fail("QUESTION_TYPE", "题型不能直接转换，请删除后新建")
    q["type"] = kind
    if "title" in data:
        q["title"] = text_field(data["title"], "题干", 500, 1)
    if "required" in data:
        q["required"] = boolean(data["required"], "required")
    if "validation" in data:
        q["validation"] = constraints(kind, data["validation"])
    elif not existing:
        q["validation"] = constraints(kind, {})
    if kind in ("SINGLE", "MULTIPLE"):
        q.setdefault("options", [])
        if "options" in data:
            options = data["options"]
            if not isinstance(options, list) or len(options) > 100:
                fail("OPTIONS_INVALID", "每题最多 100 个选项")
            old_ids = {o["optionId"] for o in (existing or {}).get("options", [])}
            result, used = [], set()
            for option in options:
                fields(option, ("optionId", "label"), ("label",))
                option_id = option.get("optionId")
                if option_id is not None and (not isinstance(option_id, str) or option_id not in old_ids):
                    fail("OPTION_INVALID", "新选项标识由服务端生成，已有选项应保留原标识")
                option_id = option_id or new_id("opt")
                if option_id in used:
                    fail("OPTION_INVALID", "选项标识不能重复")
                used.add(option_id)
                result.append({"optionId": option_id, "label": text_field(option["label"], "选项", 200, 1)})
            removed = old_ids - used
            for rule in q["jumpRules"]:
                references = {rule["value"]} if kind == "SINGLE" else set(rule["value"])
                if removed & references:
                    fail("OPTION_REFERENCED", "请先修改引用待删除选项的跳转规则", 409)
            q["options"] = result
    elif "options" in data:
        fail("OPTIONS_INVALID", "填空题不接受选项")
    return q


def jump_rules(q, data):
    if not isinstance(data, list) or len(data) > 100:
        fail("RULES_INVALID", "每题最多 100 条跳转规则")
    if q["type"] == "TEXT" and data:
        fail("RULES_INVALID", "文本题不支持条件跳转")
    existing = {r["ruleId"] for r in q["jumpRules"]}
    result, seen = [], set()
    allowed = {"SINGLE": {"EQ"}, "MULTIPLE": {"ANY_OF", "ALL_OF", "EXACT_SET"},
               "NUMBER": {"EQ", "GT", "GTE", "LT", "LTE", "BETWEEN"}, "TEXT": set()}[q["type"]]
    for entry in data:
        fields(entry, ("ruleId", "priority", "operator", "value", "target"),
               ("priority", "operator", "value", "target"))
        rule_id = entry.get("ruleId")
        if rule_id is not None and (not isinstance(rule_id, str) or rule_id not in existing):
            fail("RULES_INVALID", "新规则标识由服务端生成")
        rule_id = rule_id or new_id("rule")
        if rule_id in seen:
            fail("RULES_INVALID", "规则标识重复")
        seen.add(rule_id)
        op = entry["operator"]
        if not isinstance(op, str) or op not in allowed:
            fail("RULES_INVALID", "该题型不支持此条件")
        value = entry["value"]
        if q["type"] == "NUMBER":
            if op == "BETWEEN":
                if not isinstance(value, list) or len(value) != 2:
                    fail("RULES_INVALID", "BETWEEN 需要两个数字字符串")
                value = [decimal128(v) for v in value]
            else:
                value = decimal128(value)
        elif q["type"] == "SINGLE":
            value = text_field(value, "条件选项", 64, 1)
        else:
            if (not isinstance(value, list) or not 1 <= len(value) <= 100 or
                    any(not isinstance(v, str) for v in value) or len(set(value)) != len(value)):
                fail("RULES_INVALID", "多选条件需要不重复的选项标识数组")
        result.append({"ruleId": rule_id, "priority": int_constraint(entry["priority"], "priority"),
                       "operator": op, "value": value, "target": text_field(entry["target"], "跳转目标", 64, 1)})
    return sorted(result, key=lambda r: r["priority"])


def validate_definition(definition, publishing=False):
    questions = definition["questions"]
    if len(questions) > 200 or publishing and not questions:
        fail("DEFINITION_INVALID", "问卷需要 1～200 道题才能发布")
    positions = {q["questionId"]: i for i, q in enumerate(questions)}
    for i, q in enumerate(questions):
        try:
            v, kind = q["validation"], q["type"]
            if kind in ("SINGLE", "MULTIPLE"):
                options = {o["optionId"] for o in q["options"]}
                if publishing and not options:
                    fail("OPTIONS_EMPTY", "选择题至少需要一个选项")
                if kind == "MULTIPLE":
                    lo, hi = v.get("minSelections", 0), v.get("maxSelections", len(options))
                    exact = v.get("exactSelections")
                    if (lo > hi or publishing and hi > len(options) or
                            exact is not None and not lo <= exact <= hi or
                            publishing and q["required"] and (hi == 0 or exact == 0)):
                        fail("CONSTRAINT_INVALID", "多选数量限制冲突或超过选项数")
            if kind == "TEXT":
                if v.get("minLength", 0) > v.get("maxLength", 10000) or v.get("maxLength", 10000) > 10000:
                    fail("CONSTRAINT_INVALID", "文本长度范围冲突，最多 10000 字")
            if kind == "NUMBER":
                lo, hi = v.get("minValue"), v.get("maxValue")
                if lo is not None and hi is not None and as_decimal(lo) > as_decimal(hi):
                    fail("CONSTRAINT_INVALID", "数字最小值不能大于最大值")
                if publishing and v["integerOnly"] and lo is not None and hi is not None:
                    from decimal import ROUND_CEILING, ROUND_FLOOR
                    if as_decimal(lo).to_integral_value(rounding=ROUND_CEILING) > as_decimal(hi).to_integral_value(rounding=ROUND_FLOOR):
                        fail("CONSTRAINT_INVALID", "数字范围内不存在整数")
            priorities = set()
            for rule in q["jumpRules"]:
                if rule["priority"] in priorities:
                    fail("RULES_INVALID", "同一题的规则优先级不能重复")
                priorities.add(rule["priority"])
                target = rule["target"]
                if target != "END" and (target not in positions or positions[target] <= i):
                    fail("RULES_INVALID", "跳转目标必须为后续题目或 END")
                if kind == "SINGLE" and rule["value"] not in options:
                    fail("RULES_INVALID", "跳转条件引用了不存在的选项")
                if kind == "MULTIPLE" and not set(rule["value"]) <= options:
                    fail("RULES_INVALID", "跳转条件引用了不存在的选项")
                if kind == "NUMBER" and rule["operator"] == "BETWEEN" and as_decimal(rule["value"][0]) > as_decimal(rule["value"][1]):
                    fail("RULES_INVALID", "BETWEEN 下界不能大于上界")
        except Exception as exc:
            from .common import Problem
            if isinstance(exc, Problem):
                exc.details = [{"questionId": q["questionId"], "code": exc.code}]
            raise
    # A lower limit leaves space for the containing survey/response metadata.
    if len(BSON.encode({"definition": definition})) > 8 * 1024 * 1024:
        fail("DOCUMENT_TOO_LARGE", "问卷定义超过 8 MiB")


def answer_value(q, value):
    kind, validation = q["type"], q["validation"]
    if kind == "SINGLE":
        if value is not None and (not isinstance(value, str) or value not in {o["optionId"] for o in q["options"]}):
            fail("OPTION_INVALID", "请选择本题的有效选项")
    elif kind == "MULTIPLE":
        if (not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) for v in value) or
                len(value) != len(set(value)) or not set(value) <= {o["optionId"] for o in q["options"]}):
            fail("OPTION_INVALID", "多选答案含有重复或无效选项")
        value = [o["optionId"] for o in q["options"] if o["optionId"] in value]
        if value and (len(value) < validation.get("minSelections", 0) or
                      len(value) > validation.get("maxSelections", 100) or
                      "exactSelections" in validation and len(value) != validation["exactSelections"]):
            fail("SELECTION_COUNT", "选择数量不符合要求")
    elif kind == "TEXT":
        value = text_field(value, "文本答案", 10000)
        if value and not validation.get("minLength", 0) <= len(value) <= validation.get("maxLength", 10000):
            fail("TEXT_LENGTH", "文本长度不符合要求")
    elif kind == "NUMBER" and value is not None:
        value = decimal128(value)
        number = as_decimal(value)
        if ("minValue" in validation and number < as_decimal(validation["minValue"]) or
                "maxValue" in validation and number > as_decimal(validation["maxValue"])):
            fail("NUMBER_RANGE", "数字超出允许范围")
        if validation["integerOnly"] and number != number.to_integral_value():
            fail("INTEGER_REQUIRED", "本题只允许整数")
    return value


def matches(rule, value):
    op, expected = rule["operator"], rule["value"]
    if op == "ANY_OF":
        return bool(set(value) & set(expected))
    if op == "ALL_OF":
        return set(expected) <= set(value)
    if op == "EXACT_SET":
        return set(value) == set(expected)
    value = as_decimal(value)
    if op == "BETWEEN":
        return as_decimal(expected[0]) <= value <= as_decimal(expected[1])
    expected = as_decimal(expected)
    if op == "EQ":
        return value == expected
    if op == "GT":
        return value > expected
    if op == "GTE":
        return value >= expected
    if op == "LT":
        return value < expected
    return value <= expected


def canonical_answers(definition, entries, complete=False):
    if not isinstance(entries, list) or len(entries) > 200:
        fail("ANSWER_INVALID", "answers 应为最多 200 项的数组")
    questions = definition["questions"]
    by_id = {q["questionId"]: q for q in questions}
    supplied = {}
    for entry in entries:
        fields(entry, ("questionId", "value", "type"), ("questionId", "value"))
        qid = entry["questionId"]
        if not isinstance(qid, str) or qid not in by_id or qid in supplied:
            fail("ANSWER_INVALID", "答案包含未知或重复题目标识")
        if "type" in entry and entry["type"] != by_id[qid]["type"]:
            fail("ANSWER_INVALID", "答案题型不匹配")
        supplied[qid] = entry["value"]
    saved, visited, position, next_id = [], [], 0, None
    positions = {q["questionId"]: i for i, q in enumerate(questions)}
    while position < len(questions):
        q = questions[position]
        qid = q["questionId"]
        visited.append(qid)
        if qid not in supplied:
            next_id = qid
            break
        try:
            value = answer_value(q, supplied[qid])
        except Exception as exc:
            from .common import Problem
            if isinstance(exc, Problem):
                exc.details = [{"questionId": qid, "code": exc.code}]
            raise
        saved.append({"questionId": qid, "type": q["type"], "value": value})
        if q["required"] and empty(value):
            next_id = qid
            break
        target = None
        if not empty(value):
            target = next((r["target"] for r in sorted(q["jumpRules"], key=lambda x: x["priority"]) if matches(r, value)), None)
        if target == "END":
            break
        position = positions[target] if target else position + 1
    if complete and next_id is not None:
        fail("ANSWER_REQUIRED", "请完成实际填写路径中的必答题，并明确跳过可选题", details=[{"questionId": next_id, "code": "ANSWER_REQUIRED"}])
    kept = {a["questionId"] for a in saved}
    return {"answers": saved, "visitedQuestionIds": visited, "nextQuestionId": next_id,
            "removedQuestionIds": [qid for qid in supplied if qid not in kept]}
