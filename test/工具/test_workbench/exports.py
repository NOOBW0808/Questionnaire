"""Current observations and baseline comparisons; never import old passes."""
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import io
from pathlib import Path
import uuid
import zipfile

from .common import encoded, classification

STATUS_NAMES = {'not_run': '未执行', 'passed': '成功', 'failed': '失败',
                'blocked': '阻塞', 'skipped': '跳过'}


def actual_results(run):
    rows = []
    for case in run['catalog']['cases']:
        saved = run['results'].get(case['id'], {}) if case['execution'] == 'manual' else {}
        status = saved.get('status', 'not_run')
        if case['automation']['state'] == 'not_implemented':
            status = 'not_implemented'
        baseline = run['baseline']['cases'].get(case['id'], {})
        rows.append({'caseId': case['id'], 'title': case['title'], 'module': case['module'],
                     'phase': case['phase'], 'execution': case['execution'],
                     'status': status, 'actual': saved.get('actual', ''),
                     'startedAt': saved.get('startedAt'), 'updatedAt': saved.get('updatedAt'),
                     'evidenceOrigin': deepcopy(saved.get('evidenceOrigin')),
                     'attachments': deepcopy(saved.get('attachments', [])),
                     'baseline': baseline, 'change': classification(status, baseline.get('status'))})
    return {'schemaVersion': 2, 'kind': 'current-manual-only-api-not-imported',
            'run': {k: v for k, v in run.items() if k not in ('catalog', 'baseline', 'results')},
            'catalogVersion': run['catalog']['catalogVersion'],
            'summary': summarize(rows), 'results': rows}


def summarize(rows):
    changes = Counter(r['change'] for r in rows)
    return {'total': len(rows), 'statuses': dict(Counter(r['status'] for r in rows)),
            'changes': {name: changes[name] for name in ('新增失败', '原有失败', '已修复项', '跳过项',
                        '阻塞', '未执行', '待编写脚本', '保持通过', '新增通过')},
            'manualTotal': sum(r['execution'] == 'manual' for r in rows),
            'apiExecuted': sum(r['execution'] == 'api' and r['status'] in ('passed', 'failed', 'broken') for r in rows),
            'complete': all(r['status'] not in ('not_run', 'not_implemented', 'blocked', 'broken') for r in rows),
            'allPassed': bool(rows) and all(r['status'] == 'passed' for r in rows)}


def evidence_file(directory, item):
    """Check containment and bytes before any export, including manual-only ZIP."""
    directory = Path(directory).resolve()
    path = (directory / item['path']).resolve()
    if not path.is_relative_to(directory / 'attachments') or not path.is_file():
        raise ValueError('截图路径无效或文件丢失：' + item['name'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('截图哈希不符：' + item['name'])
    return path


def millis(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def make_allure(case, row, run, write, directory):
    """One real case per result. Inactive rows render skipped with explicit labels."""
    cid = case['id']
    attachments = []
    for item in row.get('attachments', []):
        path = evidence_file(directory, item)
        target = uuid.uuid4().hex + '-attachment.' + path.suffix.lstrip('.')
        write(target, path.read_bytes())
        attachments.append({'name': item['name'], 'source': target, 'type': item['contentType']})
    for name, value in [('完整用例规格', case), ('本轮实际结果与基线对比', row),
                        ('批次与版本凭据', {k: run[k] for k in ('id', 'name', 'catalogSha256', 'baselineSha256', 'implementationSha256')})]:
        target = uuid.uuid4().hex + '-attachment.json'
        write(target, encoded(value))
        attachments.append({'name': name, 'source': target, 'type': 'application/json'})
    status = row['status']
    stable = hashlib.sha256(('questionnaire.' + cid).encode()).hexdigest()
    result = {'uuid': uuid.uuid4().hex, 'historyId': stable, 'testCaseId': stable,
              'name': cid + ' ' + case['title'], 'fullName': 'questionnaire.' + cid,
              'status': status if status in ('passed', 'failed', 'broken') else 'broken' if status == 'blocked' else 'skipped',
              'stage': 'finished', 'description': '前置条件：\n' + '\n'.join(case['preconditions']) +
              '\n完整输入、步骤及逐步预期见附件。历史结果仅作比较，未继承为本轮通过。' +
              ('\n实际现象（用户原批次人工记录，已校验同规格/同业务代码，未由自动化重测）：\n' + row['actual']
               if row.get('evidenceOrigin') else '\n本轮实际现象：\n' + row.get('actual','')),
              'start': millis(row.get('startedAt') or run['createdAt']),
              'stop': millis(row.get('updatedAt') or run['createdAt']),
              'labels': [{'name': k, 'value': str(v)} for k, v in {
                  'parentSuite': '第一阶段回归' if case['phase'] == 1 else '第二阶段增量',
                  'suite': case['module'], 'subSuite': '手工测试' if case['execution'] == 'manual' else 'API自动化',
                  'caseId': cid, 'phase': case['phase'], 'execution': case['execution'],
                  'currentStatus': status, 'change': row['change'], 'baselineStatus': row['baseline'].get('status', '无基线'),
                  'cycleId': run['id'], 'owner': run['tester']}.items()], 'attachments': attachments}
    if status != 'passed':
        result['statusDetails'] = {'message': '[' + row['change'] + '] ' + (row['actual'] or '本轮未执行；不能沿用旧报告通过'),
                                   'trace': '真实状态：' + status}
    return result


def result_zip(run, directory, allure=False):
    stream = io.BytesIO()
    summary = actual_results(run)
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        if not allure:
            archive.writestr('actual-results.json', encoded(summary))
            archive.writestr('cases.json', encoded(run['catalog']))
            archive.writestr('baseline-comparison-only.json', encoded(run['baseline']))
            for row in summary['results']:
                for item in row['attachments']:
                    archive.write(evidence_file(directory, item), item['path'])
        else:
            for case, row in zip(run['catalog']['cases'], summary['results']):
                if case['execution'] != 'manual':
                    continue
                result = make_allure(case, row, run, archive.writestr, directory)
                archive.writestr(result['uuid'] + '-result.json', encoded(result))
            archive.writestr('manual-summary.json', encoded(summary))
    stream.seek(0)
    return stream
