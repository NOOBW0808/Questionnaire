import os
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, current_app, g, request
from pymongo.errors import DuplicateKeyError

from .common import body, digest, fail, fingerprint, reply, text_field, utcnow, plain
from .database import ROOT

auth = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))
_rates = OrderedDict()
_lock = threading.Lock()


def db():
    return current_app.extensions["database"].db


def rate_limit(category, limit=30, seconds=60):
    # Process-local limiter for the single-process course deployment; do not trust X-Forwarded-For.
    key = (category, str(g.user["_id"]) if getattr(g, "user", None) else request.remote_addr)
    now = time.monotonic()
    with _lock:
        history = _rates.setdefault(key, deque())
        while history and history[0] < now - seconds:
            history.popleft()
        if len(history) >= limit:
            fail("RATE_LIMITED", "操作过于频繁，请稍后重试", 429)
        history.append(now)
        _rates.move_to_end(key)
        while len(_rates) > 10000:
            _rates.popitem(last=False)


def origin_check():
    origin = request.headers.get("Origin")
    expected = current_app.config["APP_ORIGIN"]
    if origin and origin.rstrip("/") != expected:
        fail("ORIGIN_DENIED", "请求来源不允许", 403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        fail("ORIGIN_DENIED", "不允许跨站操作", 403)
    referer = request.headers.get("Referer")
    if not origin and referer:
        parts = urlsplit(referer)
        if f"{parts.scheme}://{parts.netloc}" != expected:
            fail("ORIGIN_DENIED", "请求来源不允许", 403)


def authenticated(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = request.cookies.get("survey_session", "")
        session = db().sessions.find_one({"tokenHash": digest(token), "expiresAt": {"$gt": utcnow()}}) if token and len(token) <= 256 else None
        user = db().users.find_one({"_id": session["userId"]}) if session else None
        if not user:
            fail("UNAUTHENTICATED", "请先登录", 401)
        g.user, g.session = user, session
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin_check()
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied.isascii() or not secrets.compare_digest(supplied, session["csrfToken"]):
                fail("CSRF_INVALID", "登录状态已变更，请刷新页面", 403)
        return func(*args, **kwargs)
    return wrapper


def user_view(user):
    return {"userId": user["_id"], "username": user["username"], "createdAt": user["createdAt"]}


def credentials(data):
    username = text_field(data["username"], "用户名", 64, 1)
    key = unicodedata.normalize("NFKC", username).casefold()
    if not 1 <= len(key) <= 128:
        fail("USERNAME_INVALID", "规范化后的用户名过长")
    password = data["password"]
    if (not isinstance(password, str) or not 8 <= len(password) <= 128 or
            any(0xD800 <= ord(c) <= 0xDFFF for c in password)):
        fail("PASSWORD_INVALID", "密码长度应为 8～128 个字符")
    return username, key, password


@auth.post("/register")
def register():
    origin_check()
    rate_limit("register", 10)
    username, key, password = credentials(body(("username", "password"), ("username", "password")))
    from bson import ObjectId
    user = {"_id": ObjectId(), "schemaVersion": 1, "username": username, "usernameKey": key,
            "passwordHash": hasher.hash(password), "createdAt": utcnow()}
    try:
        db().users.insert_one(user)
    except DuplicateKeyError:
        fail("USERNAME_EXISTS", "用户名已被使用", 409)
    return reply(user_view(user), 201)


@auth.post("/login")
def login():
    origin_check()
    rate_limit("login", 20)
    _, key, password = credentials(body(("username", "password"), ("username", "password")))
    user = db().users.find_one({"usernameKey": key})
    try:
        hasher.verify(user["passwordHash"] if user else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        fail("LOGIN_FAILED", "用户名或密码错误", 401)
    if not user:
        fail("LOGIN_FAILED", "用户名或密码错误", 401)
    if hasher.check_needs_rehash(user["passwordHash"]):
        db().users.update_one({"_id": user["_id"]}, {"$set": {"passwordHash": hasher.hash(password)}})
    old = request.cookies.get("survey_session")
    if old:
        db().sessions.delete_one({"tokenHash": digest(old)})
    token, csrf, now = secrets.token_urlsafe(48), secrets.token_urlsafe(32), utcnow()
    expires = now + timedelta(hours=current_app.config["SESSION_HOURS"])
    db().sessions.insert_one({"schemaVersion": 1, "userId": user["_id"], "tokenHash": digest(token),
                              "csrfToken": csrf, "createdAt": now, "expiresAt": expires})
    response = reply({"user": user_view(user), "csrfToken": csrf})
    response.set_cookie("survey_session", token, httponly=True, secure=current_app.config["COOKIE_SECURE"],
                        samesite="Lax", expires=expires, path="/")
    return response


@auth.get("/me")
@authenticated
def me():
    return reply({"user": user_view(g.user), "csrfToken": g.session["csrfToken"]})


@auth.post("/logout")
@authenticated
def logout():
    body(())
    db().sessions.delete_one({"_id": g.session["_id"]})
    response = reply(status=204)
    response.delete_cookie("survey_session", path="/")
    return response


class Cursors:
    def __init__(self):
        key = os.getenv("CURSOR_KEY")
        if not key:
            path = ROOT / ".local" / "cursor.key"
            path.parent.mkdir(exist_ok=True)
            try:
                with path.open("xb") as file:
                    file.write(Fernet.generate_key())
            except FileExistsError:
                pass
            key = path.read_bytes()
        self.fernet = Fernet(key)

    def encode(self, scope, position):
        import json
        return self.fernet.encrypt(json.dumps({"scope": fingerprint(scope), "position": plain(position)}).encode()).decode()

    def decode(self, value, scope):
        import json
        if not value:
            return None
        try:
            if len(value) > 4096:
                raise ValueError()
            data = json.loads(self.fernet.decrypt(value.encode(), ttl=86400))
            if data["scope"] != fingerprint(scope):
                raise ValueError()
            return data["position"]
        except (ValueError, InvalidToken, KeyError, TypeError):
            fail("CURSOR_INVALID", "分页游标无效或已过期，请重新加载", 400)
