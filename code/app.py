r"""Run this file directly in PyCharm, or use: .venv\Scripts\python.exe app.py."""
import os

from dotenv import load_dotenv

load_dotenv()

from survey_app import create_app  # noqa: E402


if __name__ == "__main__":
    from waitress import serve

    application = create_app()
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    print(f"在线问卷系统：http://{host}:{port}（Ctrl+C 停止）", flush=True)
    serve(application, host=host, port=port, threads=8, max_request_body_size=8 * 1024 * 1024)
