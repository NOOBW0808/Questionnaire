"""Manage an isolated, localhost-only MongoDB replica set; never uses the system service."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import AutoReconnect, ConnectionFailure, OperationFailure

ROOT = Path(__file__).resolve().parent.parent


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run():
    parser = argparse.ArgumentParser(description="启动/停止此项目自己的本地 MongoDB 副本集")
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--port", type=int, default=27018)
    parser.add_argument("--replica-set", default="survey-rs")
    parser.add_argument("--mongod", default=os.getenv("MONGOD_EXE", r"D:\MongoDB\bin\mongod.exe"))
    args = parser.parse_args()
    folder = ROOT / ".local" / f"mongodb-{args.port}"
    data_path = folder / "data"
    uri = f"mongodb://127.0.0.1:{args.port}/?directConnection=true"
    if not 1024 <= args.port <= 65535:
        raise RuntimeError("端口必须位于 1024～65535")

    def verify(client):
        options = client.admin.command("getCmdLineOpts")["parsed"]
        actual = Path(options["storage"]["dbPath"]).resolve()
        replication = options.get("replication", {})
        name = replication.get("replSetName", replication.get("replSet"))
        if actual != data_path.resolve() or name != args.replica_set:
            raise RuntimeError("该端口属于其他 MongoDB 实例，拒绝操作。")

    if args.action in ("stop", "status"):
        if not port_open(args.port):
            print("项目 MongoDB 未运行。")
            return
        with MongoClient(uri, serverSelectionTimeoutMS=2000) as client:
            verify(client)
            if args.action == "status":
                hello = client.admin.command("hello")
                print(f"项目 MongoDB：端口 {args.port}，副本集 {args.replica_set}，primary={hello.get('isWritablePrimary', False)}")
                return
            try:
                client.admin.command("shutdown", force=True)
            except AutoReconnect:
                pass
        for _ in range(40):
            if not port_open(args.port):
                print("项目 MongoDB 已停止，数据保留在：" + str(data_path))
                return
            time.sleep(0.25)
        raise RuntimeError("关闭未完成，请检查项目 MongoDB 日志。")

    if port_open(args.port):
        with MongoClient(uri, serverSelectionTimeoutMS=2000) as client:
            verify(client)
            if not client.admin.command("hello").get("isWritablePrimary"):
                raise RuntimeError("项目实例尚未就绪，请检查日志。")
        print("项目 MongoDB 已在运行。")
        return
    binary = Path(args.mongod)
    if not binary.is_file():
        raise RuntimeError("找不到 mongod，请使用 --mongod 指定 MongoDB 7.0+ 的可执行文件。")
    data_path.mkdir(parents=True, exist_ok=True)
    arguments = [str(binary), "--dbpath", str(data_path), "--bind_ip", "127.0.0.1", "--port", str(args.port),
                 "--replSet", args.replica_set, "--logpath", str(folder / "mongod.log"), "--logappend"]
    with (folder / "launcher.log").open("ab") as output:
        process = subprocess.Popen(arguments, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=500) as client:
            initialized = False
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("mongod 启动失败，请检查：" + str(folder / "mongod.log"))
                try:
                    hello = client.admin.command("hello")
                    verify(client)
                    if hello.get("isWritablePrimary"):
                        (folder / "process.json").write_text(json.dumps({"pid": process.pid, "port": args.port}), "utf-8")
                        print(f"MongoDB 已就绪：mongodb://127.0.0.1:{args.port}/?replicaSet={args.replica_set}")
                        print("数据目录：" + str(data_path))
                        return
                    if not initialized:
                        try:
                            client.admin.command("replSetInitiate", {"_id": args.replica_set, "members": [{"_id": 0, "host": f"127.0.0.1:{args.port}"}]})
                        except OperationFailure as exc:
                            if exc.code != 23:  # AlreadyInitialized
                                raise
                        initialized = True
                except ConnectionFailure:
                    pass
                time.sleep(0.3)
        raise RuntimeError("MongoDB 在限定时间内未成为 primary。")
    except Exception:
        # Only terminate the exact child created above, never another service or saved PID.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        raise


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(f"操作失败：{error}", file=sys.stderr)
        sys.exit(1)
