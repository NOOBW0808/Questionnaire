"""Real HTTP helpers. No Flask test client and no business route replacements."""
import copy
import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

import allure
import requests
from bson import ObjectId, json_util

PASSWORD = "TestPass!2026"


def iso(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat(timespec="milliseconds")


def reset_rates(*categories):
    from survey_app.security import _lock, _rates
    with _lock:
        for key in list(_rates):
            if not categories or key[0] in categories:
                del _rates[key]


def redact(value):
    if isinstance(value, dict):
        return {k: "<redacted>" if k.lower() in {
            "password", "passwordhash", "csrftoken", "cookie", "set-cookie", "x-csrf-token", "tokenhash"
        } else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


def data(response, status=200):
    assert response.status_code == status, f"Expected HTTP {status}, got {response.status_code}: {response.text[:2000]}"
    assert response.headers.get("X-Request-ID"), "Missing request ID"
    assert response.headers.get("Cache-Control") == "no-store"
    return response.json().get("data") if response.content else None


def error(response, status=422, code=None):
    assert response.status_code == status, f"Expected HTTP {status}, got {response.status_code}: {response.text[:2000]}"
    body = response.json()
    assert body.get("requestId") and body["error"].get("message")
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


class Client:
    def __init__(self, ctx):
        self.ctx = ctx
        self.session = requests.Session()
        self.csrf = None
        self.uid = None
        self.username = None

    def call(self, method, path, payload=None, *, headers=None, raw=None, **kw):
        h = {"Origin": self.ctx.base}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        args = {"json": payload} if raw is None else {"data": raw, "headers": {**h, "Content-Type": "application/json"}}
        if raw is None:
            args["headers"] = h
        response = self.session.request(method, self.ctx.base + "/api/v1" + path, timeout=35, **args, **kw)
        try:
            output = response.json()
        except ValueError:
            output = response.text[:1000]
        # For raw JSON, parse before recording so escaped passwords are redacted too.
        supplied = payload
        if raw is not None:
            try:
                supplied = json.loads(raw)
            except (ValueError, TypeError):
                supplied = "<malformed JSON>"
        with self.ctx.lock:
            self.ctx.trace.append(redact({"method": method, "path": path, "input": supplied,
                "headers": h, "status": response.status_code, "output": output,
                "responseHeaders": dict(response.headers)}))
        return response

    def login(self, username, password=PASSWORD, expected=200):
        r = self.call("POST", "/auth/login", {"username": username, "password": password})
        if expected == 200:
            result = data(r)
            self.csrf = result["csrfToken"]
            self.uid, self.username = result["user"]["userId"], result["user"]["username"]
        else:
            error(r, expected)
        return r


class Context:
    def __init__(self, app, base):
        self.app, self.base = app, base
        self.db = app.extensions["database"].db
        self.trace, self.lock = [], threading.Lock()
        self._owner = None
        self.survey_ids = []

    def client(self):
        return Client(self)

    def user(self, name=None, password=PASSWORD):
        # Setup only: prevent unrelated fixture accounts exhausting the IP auth quota.
        # Tests of the limiter use Client.call directly without this reset.
        reset_rates("register", "login")
        client = self.client()
        name = name or "api_" + uuid.uuid4().hex
        d = data(client.call("POST", "/auth/register", {"username": name, "password": password}), 201)
        assert "passwordHash" not in d
        client.login(name, password)
        return client

    @property
    def owner(self):
        if self._owner is None:
            self._owner = self.user()
        return self._owner

    def form(self, questions=None, publish=True, **settings):
        s = Survey(self, self.owner, {"title": "课程体验调查", "allowRepeat": True, **settings})
        for q in default_questions() if questions is None else questions:
            s.add(q)
        if publish:
            s.publish()
        return s

    def evidence(self, name, value):
        allure.attach(json_util.dumps(redact(value), ensure_ascii=True, indent=2), name,
                      allure.attachment_type.JSON)


def default_questions():
    return [
        {"type": "SINGLE", "title": "Q1", "required": True, "options": [{"label": "继续"}, {"label": "结束"}]},
        {"type": "MULTIPLE", "title": "Q2", "required": True, "options": [{"label": x} for x in ["苹果", "香蕉", "梨"]],
         "validation": {"minSelections": 1, "maxSelections": 2}},
        {"type": "TEXT", "title": "Q3", "required": False, "validation": {"minLength": 2, "maxLength": 10}},
        {"type": "NUMBER", "title": "Q4", "required": True, "validation": {"minValue": "0", "maxValue": "120", "integerOnly": True}},
    ]


class Survey:
    def __init__(self, ctx, owner, settings):
        self.ctx, self.owner = ctx, owner
        r = owner.call("POST", "/surveys", settings)
        self.view = data(r, 201)
        self.etag = r.headers["ETag"]
        self.id, self.code = self.view["surveyId"], self.view["publicCode"]
        self.path = "/surveys/" + self.id
        ctx.survey_ids.append(ObjectId(self.id))

    @property
    def qs(self):
        return self.view["draft"]["questions"]

    def refresh(self):
        r = self.owner.call("GET", self.path)
        self.view, self.etag = data(r), r.headers["ETag"]
        return self.view

    def change(self, method, suffix, payload=None, expected=200, code=None, headers=None):
        r = self.owner.call(method, self.path + suffix, payload, headers={"If-Match": self.etag, **(headers or {})})
        if expected >= 400:
            error(r, expected, code)
        elif expected == 204:
            data(r, 204)
            self.refresh()
        else:
            self.view, self.etag = data(r, expected), r.headers["ETag"]
        return r

    def add(self, q, expected=201, **kw):
        return self.change("POST", "/questions", q, expected, **kw)

    def edit(self, i, patch, **kw):
        return self.change("PATCH", "/questions/" + self.qs[i]["questionId"], patch, **kw)

    def rule(self, i, rules, **kw):
        return self.change("PUT", "/questions/" + self.qs[i]["questionId"] + "/jump-rules", {"rules": rules}, **kw)

    def publish(self, **kw):
        return self.change("POST", "/publish", {}, **kw)

    def patch(self, patch, **kw):
        return self.change("PATCH", "", patch, **kw)

    def close(self):
        return self.change("POST", "/close", {})

    def public(self, client=None, expected=200):
        r = (client or self.owner).call("GET", "/public-surveys/" + self.code)
        return data(r) if expected == 200 else error(r, expected)

    def attempt(self, client=None, expected=201):
        client = client or self.ctx.user()
        r = client.call("POST", "/public-surveys/" + self.code + "/attempts", {})
        if expected != 201:
            error(r, expected)
            return r
        return Attempt(self, client, r)

    def stats(self, suffix="", **params):
        return data(self.owner.call("GET", self.path + suffix + "/statistics", params=params))

    def answers(self, i, **params):
        return data(self.owner.call("GET", self.path + "/questions/" + self.qs[i]["questionId"] + "/answers", params=params))


class Attempt:
    def __init__(self, survey, client, response):
        self.s, self.client = survey, client
        self.view, self.etag = data(response, 201), response.headers["ETag"]
        self.id = self.view["attemptId"]
        self.path = "/attempts/" + self.id
        self.qs = self.view["definition"]["questions"]

    def entries(self, values=None):
        values = values if values is not None else [self.qs[0]["options"][0]["optionId"],
            [self.qs[1]["options"][0]["optionId"]], "很好", "18"]
        return [{"questionId": q["questionId"], "value": v} for q, v in zip(self.qs, values)]

    def save(self, values=None, entries=None, expected=200, code=None, **kw):
        r = self.client.call("PUT", self.path + "/answers", {"answers": entries if entries is not None else self.entries(values)},
                             headers={"If-Match": self.etag, **kw})
        if expected == 200:
            self.view, self.etag = data(r), r.headers["ETag"]
        else:
            error(r, expected, code)
        return r

    def submit(self, key=None, expected=201, code=None, **headers):
        key = key or uuid.uuid4().hex
        r = self.client.call("POST", self.path + "/submit", {},
                             headers={"If-Match": self.etag, "Idempotency-Key": key, **headers})
        if expected in (200, 201):
            data(r, expected)
        else:
            error(r, expected, code)
        return r

    def refresh(self):
        r = self.client.call("GET", self.path)
        self.view, self.etag = data(r), r.headers["ETag"]
        return self.view


def rule(operator, value, target="END", priority=10):
    return {"operator": operator, "value": value, "target": target, "priority": priority}


def parallel(*calls):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        barrier = threading.Barrier(len(calls))
        def run(call):
            barrier.wait(timeout=20)
            return call()
        return list(pool.map(run, calls))
