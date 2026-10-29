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
        from .schema_tools import inspect_stage, install_empty
        self.schema_stage = inspect_stage(self.db)
        if self.schema_stage == 0:
            install_empty(self.db)
            self.schema_stage = 2

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
