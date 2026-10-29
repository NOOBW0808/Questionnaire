import hashlib
import json
from pathlib import Path

STAGE = Path(__file__).resolve().parents[2]
ROOT = STAGE.parent
CODE = ROOT / "code"
CATALOG = STAGE / '测试步骤/第二阶段/cases.json'
BASELINE = STAGE / '测试步骤/第二阶段/baseline.json'
STORAGE = ROOT / '.local/phase2-manual'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def implementation_hash():
    paths = [CODE / 'app.py', CODE / 'requirements-lock.txt']
    for name in ('survey_app', 'static', '数据库设计/第二阶段/运行规范'):
        paths += [p for p in (CODE / name).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return hashlib.sha256(encoded({p.relative_to(CODE).as_posix(): sha(p) for p in sorted(paths)})).hexdigest()


def test_suite_hash():
    paths = [p for folder in ('test/自动化脚本/第一阶段/api', 'test/自动化脚本/第二阶段')
             for p in (ROOT / folder).rglob('*.py')]
    paths += [ROOT / 'test/工具/pytest.ini']
    return hashlib.sha256(encoded({p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)})).hexdigest()


def classification(current, baseline):
    if current in ('not_run', 'not_implemented'):
        return '未执行' if current == 'not_run' else '待编写脚本'
    if current == 'blocked':
        return '阻塞'
    if current == 'skipped':
        return '跳过项'
    if current in ('failed', 'broken'):
        return '原有失败' if baseline in ('failed', 'broken') else '新增失败'
    if current == 'passed':
        return '已修复项' if baseline in ('failed', 'broken') else ('保持通过' if baseline == 'passed' else '新增通过')
    raise ValueError('Unknown status: ' + current)
