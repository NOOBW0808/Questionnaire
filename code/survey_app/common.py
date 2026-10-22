import hashlib
import json
import re
from datetime import datetime, timezone

from bson import ObjectId
from bson.decimal128 import Decimal128
from flask import current_app, g, jsonify, request


class Problem(Exception):
    def __init__(self, code, message, status=422, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details


def fail(code, message, status=422, details=None):
    raise Problem(code, message, status, details)


def utcnow():
    return datetime.now(timezone.utc)


def oid(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return ObjectId(value)
    except (ValueError, TypeError):
        fail("NOT_FOUND", "资源不存在或无权访问", 404)


def plain(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def reply(data=None, status=200, etag=None):
    if status == 204:
        response = current_app.response_class(status=204)
    else:
        response = jsonify(data=plain(data), requestId=g.request_id)
        response.status_code = status
    if etag:
        response.headers["ETag"] = etag
    return response


def body(allowed, required=()):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail("BAD_JSON", "请求体必须是 JSON 对象", 400)
    fields(data, allowed, required)
    return data


def fields(data, allowed, required=()):
    if not isinstance(data, dict):
        fail("FIELDS_INVALID", "字段必须为对象")
    if set(data) - set(allowed) or set(required) - set(data):
        fail("FIELDS_INVALID", "存在未知字段或缺少必要字段", details={
            "unknown": sorted(set(data) - set(allowed)), "missing": sorted(set(required) - set(data))})


def text_field(value, name, maximum, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        fail("FIELD_INVALID", f"{name}长度应为 {minimum}～{maximum} 个字符")
    value = value.strip()
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        fail("FIELD_INVALID", f"{name}包含无效 Unicode 字符")
    return value


def boolean(value, name):
    if type(value) is not bool:
        fail("FIELD_INVALID", f"{name}必须是布尔值")
    return value


def deadline(value):
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        fail("DEADLINE_INVALID", "截止时间须为带时区的 ISO 8601 时间或 null")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")))


def survey_etag(s):
    return f'"definition-{s["definitionVersion"]}-control-{s["controlRevision"]}"'


def attempt_etag(a):
    return f'"attempt-{a["revision"]}"'


def require_match(pattern):
    value = request.headers.get("If-Match")
    if not value:
        fail("PRECONDITION_REQUIRED", "请携带最新 If-Match 版本", 428)
    if not re.fullmatch(pattern, value):
        fail("VERSION_CONFLICT", "版本无效，请重新加载", 409)
    return value


def page_limit():
    try:
        value = int(request.args.get("limit", "20"))
        if not 1 <= value <= 100:
            raise ValueError()
        return value
    except ValueError:
        fail("PAGE_INVALID", "limit 应为 1～100 的整数", 400)
