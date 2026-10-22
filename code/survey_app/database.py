"""Driver infrastructure. Schemas are read from the authoritative design JSON."""
import json
import os
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .common import fail

ROOT = Path(__file__).resolve().parent.parent


class Database:
    def __init__(self):
        uri = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27018/?replicaSet=survey-rs")
        self.client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000,
                                  connectTimeoutMS=5000, appname="questionnaire-app")
        self.db = self.client[os.getenv("MONGODB_DATABASE", "questionnaire_app")]
        hello = self.client.admin.command("hello")
        if not hello.get("setName"):
            raise RuntimeError("需要 MongoDB 副本集以支持事务，请按 README 启动独立的本地副本集。")
        if self.client.server_info()["versionArray"][0] < 7:
            raise RuntimeError("需要 MongoDB 7.0 或更高版本。")
        self.ensure_schema()

    def ensure_schema(self):
        specification = json.loads((ROOT / "数据库设计" / "schemas.json").read_text("utf-8"))
        for name, spec in specification["collections"].items():
            info = next(self.db.list_collections(filter={"name": name}), None)
            if info is None:
                try:
                    self.db.create_collection(name, validator=spec["validator"],
                                              validationLevel="strict", validationAction="error")
                except CollectionInvalid:
                    pass
                info = next(self.db.list_collections(filter={"name": name}))
            options = info.get("options", {})
            if (options.get("validator") != spec["validator"] or
                    options.get("validationLevel", "strict") != "strict" or
                    options.get("validationAction", "error") != "error"):
                raise RuntimeError(f"集合 {name} 与 schemas.json 不兼容。请使用新的数据库名，或先备份并单独迁移；程序不会覆盖旧结构。")
            for index in spec["indexes"]:
                opts = {k: v for k, v in index.items() if k != "key"}
                self.db[name].create_index(list(index["key"].items()), **opts)
        # A response v1 database must be migrated explicitly, even if someone changed its validator.
        if self.db.responses.find_one({"schemaVersion": {"$ne": 2}}, {"_id": 1}):
            raise RuntimeError("存在旧版 responses，请先迁移或使用新数据库。")

    def transaction(self, callback):
        """Retry a new snapshot on write conflict; retry the SAME commit if its outcome is unknown."""
        end = time.monotonic() + 18
        with self.client.start_session() as session:
            for _ in range(8):
                session.start_transaction(read_concern=ReadConcern("snapshot"),
                                          write_concern=WriteConcern("majority", wtimeout=5000),
                                          max_commit_time_ms=5000)
                try:
                    result = callback(session)
                except Exception as exc:
                    if session.in_transaction:
                        try:
                            session.abort_transaction()
                        except PyMongoError:
                            pass
                    if (isinstance(exc, DuplicateKeyError) or
                            isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError")):
                        if time.monotonic() < end:
                            continue
                        fail("BUSY", "并发操作较多，请稍后重试", 409)
                    raise
                restart = False
                while True:
                    try:
                        session.commit_transaction()
                        return result
                    except PyMongoError as exc:
                        if exc.has_error_label("UnknownTransactionCommitResult"):
                            if time.monotonic() < end:
                                continue
                            fail("COMMIT_UNCERTAIN", "提交结果待确认；请使用同一幂等键重试，勿新建提交", 503)
                        if exc.has_error_label("TransientTransactionError") and time.monotonic() < end:
                            restart = True
                            break
                        raise
                if not restart:
                    break
        fail("BUSY", "并发操作较多，请稍后重试", 409)
