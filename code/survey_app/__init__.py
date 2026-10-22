import os
import secrets

from flask import Flask, g, request, send_from_directory
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from .common import Problem
from .database import Database, ROOT
from .security import Cursors, auth


def create_app():
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
    app.json.ensure_ascii = False
    app.config.update(MAX_CONTENT_LENGTH=8 * 1024 * 1024,
                      APP_ORIGIN=os.getenv("APP_ORIGIN", "http://127.0.0.1:5000").rstrip("/"),
                      COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                      SESSION_HOURS=max(1, min(720, int(os.getenv("SESSION_HOURS", "24")))))
    app.extensions["database"] = Database()
    app.extensions["cursors"] = Cursors()

    @app.before_request
    def context():
        g.request_id = "req_" + secrets.token_hex(10)
        if request.method == "OPTIONS":
            return app.response_class(status=204)

    @app.after_request
    def headers(response):
        response.headers["X-Request-ID"] = g.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Problem)
    def problem(exc):
        error = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["details"] = exc.details
        from flask import jsonify
        return jsonify(error=error, requestId=g.request_id), exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        messages = {400: "请求格式无效", 404: "资源不存在", 405: "不支持此请求方法", 413: "请求体超过 8 MiB"}
        return problem(Problem("HTTP_ERROR", messages.get(exc.code, "请求无法处理"), exc.code))

    @app.errorhandler(PyMongoError)
    def database_error(exc):
        # Never log query documents, credentials, or submitted answer content.
        app.logger.error("requestId=%s databaseError=%s code=%s", g.request_id, type(exc).__name__, getattr(exc, "code", None))
        return problem(Problem("DATABASE_UNAVAILABLE", "数据库操作失败，请稍后重试；提交请保留原幂等键", 503))

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.error("requestId=%s errorType=%s", g.request_id, type(exc).__name__)
        return problem(Problem("INTERNAL_ERROR", "服务暂时不可用，请凭 requestId 排查", 500))

    from .management import management
    from .filling import filling
    from .statistics import statistics
    for blueprint in (auth, management, filling, statistics):
        app.register_blueprint(blueprint)

    @app.get("/")
    @app.get("/survey/<public_code>")
    def index(public_code=None):
        return send_from_directory(app.static_folder, "index.html")

    return app
