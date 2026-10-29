"""Independent localhost evidence collector; it never connects to the survey DB."""
import argparse
import hashlib
import io
import json
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import warnings

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from PIL import Image, UnidentifiedImageError
from .exports import STATUS_NAMES, actual_results, result_zip
from .common import ROOT, STAGE, CATALOG, BASELINE, STORAGE, encoded, sha, implementation_hash, test_suite_hash

WEB = STAGE / "手动测试/网页"
LOCK = threading.RLock()
MAX_IMAGE = 10 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(encoded(data))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        abort(400, description=label + '不能为空或超过长度限制')
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        abort(400, description=label + '含无效字符')
    return value.strip()


def create_app(data_dir=None):
    app = Flask(__name__, static_folder=str(WEB), static_url_path='/assets')
    app.json.ensure_ascii = False
    app.config['MAX_CONTENT_LENGTH'] = MAX_IMAGE + 128 * 1024
    storage = Path(data_dir or STORAGE).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)

    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            abort(403, description='仅供本机访问')
        host = urlsplit('http://' + request.host).hostname
        if host not in ('127.0.0.1', 'localhost', '::1'):
            abort(403, description='无效主机')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
                abort(403, description='不允许跨站写入')
            supplied = request.headers.get('X-Collector-Token', '')
            if not supplied.isascii() or not secrets.compare_digest(supplied, token):
                abort(403, description='请刷新收集页面后重试')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(HTTPException)
    def error(exc):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(ValueError)
    def invalid_export(exc):
        return jsonify(error=str(exc)), 400

    def load(run_id):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            abort(404)
        directory = storage / run_id
        path = directory / 'run.json'
        if not path.is_file():
            abort(404, description='测试批次不存在')
        return read_json(path), directory

    def manual_case(run, case_id):
        case = next((c for c in run['catalog']['cases'] if c['id'] == case_id and c['execution'] == 'manual'), None)
        if not case:
            abort(404, description='手动用例不存在')
        return case

    def checked_revision(run, supplied):
        if str(run['revision']) != str(supplied):
            abort(409, description='批次已在其他页面更新，请重新载入；当前编辑内容仍保留在页面')

    def persist(run, directory):
        run['revision'] += 1
        run['updatedAt'] = now()
        write_json(directory / 'run.json', run)

    def row_for(run, case_id):
        return run['results'].setdefault(case_id, {'status': 'not_run', 'actual': '',
                                                  'startedAt': now(), 'updatedAt': now(), 'attachments': []})

    @app.get('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.get('/api/bootstrap')
    def bootstrap():
        with LOCK:
            runs = [read_json(p) for p in storage.glob('*/run.json')]
        runs.sort(key=lambda x: x['createdAt'], reverse=True)
        # Old frozen batches remain on disk as evidence, not as current selectable cases.
        current_hash = sha(CATALOG)
        archived_count = sum(r['catalogSha256'] != current_hash for r in runs)
        runs = [r for r in runs if r['catalogSha256'] == current_hash]
        return jsonify(token=token, catalog=read_json(CATALOG), baseline=read_json(BASELINE),
                       archivedRunCount=archived_count,
                       runs=[{k: r[k] for k in ('id', 'name', 'tester', 'createdAt')} for r in runs])

    @app.post('/api/runs')
    def create_run():
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400)
        metadata = {k: text(data.get(k), name, limit) for k, name, limit in
                    [('name', '批次名', 120), ('tester', '执行人', 80),
                     ('targetUrl', '被测系统地址', 1000), ('environment', '浏览器与环境', 2000)]}
        if urlsplit(metadata['targetUrl']).scheme not in ('http', 'https') or not urlsplit(metadata['targetUrl']).hostname:
            abort(400, description='被测系统地址应为有效的http或https地址')
        catalog_bytes = (CATALOG).read_bytes()
        run = {'schemaVersion': 1, 'id': uuid.uuid4().hex, **metadata, 'revision': 0,
               'createdAt': now(), 'updatedAt': now(), 'catalogSha256': hashlib.sha256(catalog_bytes).hexdigest(),
               'catalog': json.loads(catalog_bytes), 'baseline': read_json(BASELINE),
               'baselineSha256': sha(BASELINE), 'implementationSha256': implementation_hash(),
               'testSuiteSha256': test_suite_hash(),
               'results': {}}
        with LOCK:
            directory = storage / run['id']
            directory.mkdir()
            (directory / 'attachments').mkdir()
            write_json(directory / 'run.json', run)
        return jsonify(run), 201

    @app.get('/api/runs/<run_id>')
    def get_run(run_id):
        with LOCK:
            run, _ = load(run_id)
            return jsonify(run)

    @app.put('/api/runs/<run_id>/cases/<case_id>')
    def save_result(run_id, case_id):
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400)
        status = data.get('status')
        if status not in STATUS_NAMES:
            abort(400, description='请选择合法执行状态')
        actual = data.get('actual', '')
        actual = text(actual, '实际结果或原因', 20000) if status != 'not_run' or actual else ''
        with LOCK:
            run, directory = load(run_id)
            manual_case(run, case_id)
            checked_revision(run, data.get('revision'))
            row = row_for(run, case_id)
            if status in ('passed', 'failed') and not row['attachments']:
                abort(400, description='通过或失败需先上传至少一张实验截图')
            row.update(status=status, actual=actual, updatedAt=now())
            persist(run, directory)
            return jsonify(run)

    @app.post('/api/runs/<run_id>/cases/<case_id>/attachments')
    def upload(run_id, case_id):
        file = request.files.get('file')
        if not file:
            abort(400, description='请选择截图')
        data = file.read(MAX_IMAGE + 1)
        if not data or len(data) > MAX_IMAGE:
            abort(400, description='单张截图须为1字节至10MiB')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    extension, mime = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'),
                                       'WEBP': ('webp', 'image/webp')}[image.format]
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
        except (OSError, ValueError, KeyError, SyntaxError, UnidentifiedImageError,
                Image.DecompressionBombWarning, Image.DecompressionBombError):
            abort(400, description='仅接收PNG、JPEG、WebP图片，请勿只修改文件扩展名')
        with LOCK:
            run, directory = load(run_id)
            manual_case(run, case_id)
            checked_revision(run, request.form.get('revision'))
            row = row_for(run, case_id)
            if len(row['attachments']) >= 30:
                abort(400, description='单用例最多30张截图')
            item_id = uuid.uuid4().hex
            name = (file.filename or '截图').replace('\\', '/').rsplit('/', 1)[-1][:200]
            item = {'id': item_id, 'name': name, 'path': 'attachments/' + item_id + '.' + extension,
                    'contentType': mime, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'uploadedAt': now()}
            (directory / item['path']).write_bytes(data)
            row['attachments'].append(item)
            row['updatedAt'] = now()
            persist(run, directory)
            return jsonify(run), 201

    @app.get('/api/runs/<run_id>/attachments/<item_id>')
    def image_file(run_id, item_id):
        with LOCK:
            run, directory = load(run_id)
            item = next((a for r in run['results'].values() for a in r['attachments'] if a['id'] == item_id), None)
            if not item:
                abort(404)
            return send_file(directory / item['path'], mimetype=item['contentType'])

    @app.delete('/api/runs/<run_id>/cases/<case_id>/attachments/<item_id>')
    def delete_image(run_id, case_id, item_id):
        with LOCK:
            run, directory = load(run_id)
            manual_case(run, case_id)
            checked_revision(run, request.headers.get('X-Run-Revision'))
            row = row_for(run, case_id)
            item = next((a for a in row['attachments'] if a['id'] == item_id), None)
            if not item:
                abort(404)
            if row['status'] in ('passed', 'failed') and len(row['attachments']) == 1:
                abort(400, description='删除最后一张证据前，请先将本用例保存为未执行')
            row['attachments'].remove(item)
            row['updatedAt'] = now()
            persist(run, directory)
            (directory / item['path']).unlink(missing_ok=True)
            return jsonify(run)

    @app.get('/api/runs/<run_id>/export/<kind>')
    def export(run_id, kind):
        with LOCK:
            run, directory = load(run_id)
            if kind == 'json':
                response = app.response_class(encoded(actual_results(run)), mimetype='application/json')
                response.headers['Content-Disposition'] = 'attachment; filename="actual-results-' + run_id + '.json"'
                return response
            if kind not in ('bundle', 'allure'):
                abort(404)
            stream = result_zip(run, directory, allure=kind == 'allure')
        return send_file(stream, mimetype='application/zip', as_attachment=True,
                         download_name=('allure-results-' if kind == 'allure' else 'manual-results-') + run_id + '.zip')

    return app


def main():
    parser = argparse.ArgumentParser(description='手动测试截图与结果收集工具')
    parser.add_argument('--port', type=int, default=5081)
    parser.add_argument('--data-dir', type=Path, default=STORAGE)
    args = parser.parse_args()
    from waitress import serve
    print(f'手动测试收集页：http://127.0.0.1:{args.port}\n数据目录：{args.data_dir.resolve()}', flush=True)
    serve(create_app(args.data_dir), host='127.0.0.1', port=args.port, threads=4)
