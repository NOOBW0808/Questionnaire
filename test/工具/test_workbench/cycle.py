"""Run fresh APIs, then combine only this cycle's real manual observations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

from .common import ROOT, CODE, STAGE, CATALOG, BASELINE, STORAGE, read, encoded, sha, implementation_hash, test_suite_hash, classification
from .exports import actual_results, summarize, make_allure


def unique():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]


def check_run(run):
    for key, current in [('catalogSha256', sha(CATALOG)), ('baselineSha256', sha(BASELINE)),
                         ('implementationSha256', implementation_hash()), ('testSuiteSha256', test_suite_hash())]:
        if run[key] != current:
            raise ValueError(key + ' 已改变；请新建批次重新执行，禁止混入不同规格/实现的结果')


def run_apis(run, mongo):
    from pymongo import MongoClient
    with MongoClient(mongo, serverSelectionTimeoutMS=5000) as client:
        h = client.admin.command('hello')
        if not (h.get('setName') and h.get('isWritablePrimary')):
            raise ValueError('需要可写MongoDB副本集；未执行任何业务库迁移')
    folder = ROOT / 'test/执行记录/完整系统回归/本地运行' / ('allure-cycle-' + unique())
    folder.mkdir(parents=True)
    manifest = {k: run[k] for k in ('id', 'catalogSha256', 'baselineSha256', 'implementationSha256', 'testSuiteSha256')}
    manifest.update(kind='fresh-api-cycle', createdAt=datetime.now(timezone.utc).isoformat(), batches={})
    (folder / 'catalog.json').write_bytes(encoded(run['catalog']))
    (folder / 'baseline.json').write_bytes(encoded(run['baseline']))
    (folder / 'cycle.json').write_bytes(encoded(manifest))
    env = os.environ.copy()
    env.update(MONGODB_URI=mongo, COOKIE_SECURE='false', PYTHONIOENCODING='utf-8',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH=os.pathsep.join([str(CODE), str(STAGE / "工具")]),
               CYCLE_ID=run['id'], CYCLE_CATALOG=str(folder / 'catalog.json'))
    env.pop('KEEP_TEST_DATABASE', None)
    for phase, path in [('phase1', ROOT / 'test/自动化脚本/第一阶段/api'), ('phase2', STAGE / '自动化脚本/第二阶段')]:
        number = int(phase[-1])
        expected = sum(c['phase'] == number and c['execution'] == 'api' and c['automation']['state'] == 'implemented'
                       for c in run['catalog']['cases'])
        output = folder / phase
        output.mkdir()
        env.update(CYCLE_OBSERVATIONS=str(output / 'observations.json'), API_RUNTIME_JSON=str(output / 'runtime.json'))
        cmd = [sys.executable, '-X', 'utf8', '-m', 'pytest', '-c', str(ROOT / 'test/工具/pytest.ini'),
               '-p', 'allure_pytest.plugin', '-p', 'test_workbench.pytest_capture', str(path),
               '-q', '--tb=short', '--alluredir', str(output / 'allure-results'), '--junitxml', str(output / 'junit.xml')]
        print('Running ' + phase + ' (' + str(expected) + ' API cases)', flush=True)
        with (output / 'pytest.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen(cmd, cwd=ROOT / 'test', env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in proc.stdout:
                print(line, end='', flush=True)
                log.write(line)
            code = proc.wait()
        manifest['batches'][phase] = {'exitCode': code, 'expected': expected}
        (folder / 'cycle.json').write_bytes(encoded(manifest))
    manifest['evidenceSha256'] = {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file() and p.name != 'cycle.json'}
    (folder / 'cycle.json').write_bytes(encoded(manifest))
    return folder


def load_api(folder, run):
    manifest = read(folder / 'cycle.json')
    if manifest.get('kind') != 'fresh-api-cycle':
        raise ValueError('不是本工具生成的新一轮API结果，禁止沿用历史执行目录')
    for key in ('id', 'catalogSha256', 'baselineSha256', 'implementationSha256', 'testSuiteSha256'):
        if run[key] != manifest[key]:
            raise ValueError('API与手工批次不一致：' + key)
    if set(manifest['batches']) != {'phase1', 'phase2'}:
        raise ValueError('API轮次尚未完成；不得汇总为完整执行')
    actual_files = {p.relative_to(folder).as_posix() for phase in ('phase1', 'phase2')
                    for p in (folder / phase).rglob('*') if p.is_file()}
    expected_files = {n for n in manifest['evidenceSha256'] if n.startswith(('phase1/', 'phase2/'))}
    if actual_files != expected_files:
        raise ValueError('API证据文件清单改变；禁止混入额外结果或遗漏文件')
    for name, expected in manifest['evidenceSha256'].items():
        p = (folder / name).resolve()
        if not p.is_relative_to(folder.resolve()) or sha(p) != expected:
            raise ValueError('API证据哈希或路径异常：' + name)
    rows, raw = {}, {}
    for phase in ('phase1', 'phase2'):
        p = folder / phase / 'observations.json'
        if p.is_file():
            doc = read(p)
            if doc['cycleId'] != run['id']:
                raise ValueError('API观察记录批次不符')
            for cid, row in doc['results'].items():
                if cid in rows:
                    raise ValueError('API重复用例：' + cid)
                rows[cid] = row
        for p in (folder / phase / 'allure-results').glob('*-result.json'):
            result = read(p)
            ids = {x['value'] for x in result.get('labels', []) if x['name'] == 'caseId'}
            cycles = {x['value'] for x in result.get('labels', []) if x['name'] == 'cycleId'}
            if len(ids) != 1 or cycles != {run['id']}:
                raise ValueError('Allure结果缺少唯一caseId/本轮凭据：' + p.name)
            cid = ids.pop()
            if cid in raw:
                raise ValueError('Allure重复用例：' + cid)
            raw[cid] = result
    expected = {c['id'] for c in run['catalog']['cases'] if c['execution'] == 'api' and c['automation']['state'] == 'implemented'}
    if (set(rows) | set(raw)) - expected:
        raise ValueError('API出现目录以外的用例')
    # Missing results are represented as broken below, never as passed.
    return rows, raw


def merge(run, directory, api_folder, allure_cli=None):
    check_run(run)
    api_rows, raw = load_api(api_folder, run)
    folder = api_folder / ('combined-' + unique())
    results = folder / 'allure-results'
    results.mkdir(parents=True)
    summary = actual_results(run)
    summary.update(kind='fresh-manual-and-api-with-baseline-comparison', apiCycle=api_folder.name)
    specs = {c['id']: c for c in run['catalog']['cases']}
    for row in summary['results']:
        cid = row['caseId']
        if row['execution'] == 'api' and specs[cid]['automation']['state'] == 'implemented':
            item = api_rows.get(cid)
            if not item or cid not in raw:
                row.update(status='broken', actual='本轮API缺失观察/Allure结果；检查基础设施与日志，禁止沿用历史通过')
            else:
                row.update({k: item[k] for k in ('status', 'actual', 'startedAt', 'updatedAt', 'reports')})
        row['change'] = classification(row['status'], row['baseline'].get('status'))
    summary['summary'] = summarize(summary['results'])
    summary['summary']['apiScheduled'] = sum(c['execution']=='api' and c['automation']['state']=='implemented' for c in specs.values())
    (folder / 'actual-results.json').write_bytes(encoded(summary))
    (folder / 'baseline-comparison-only.json').write_bytes(encoded(run['baseline']))
    # Copy current raw attachments/containers only; no previous-run history or pass results.
    for phase in ('phase1', 'phase2'):
        for p in (api_folder / phase / 'allure-results').glob('*'):
            if p.is_file() and not p.name.endswith('-result.json'):
                target = results / p.name
                if target.exists():
                    raise ValueError('Allure附件重名：' + p.name)
                shutil.copyfile(p, target)
    for row in summary['results']:
        cid = row['caseId']
        if cid in raw and row['execution'] == 'api':
            result = raw[cid]
            result['status'] = row['status']
            result['historyId'] = result['testCaseId'] = hashlib.sha256(('questionnaire.' + cid).encode()).hexdigest()
            result['fullName'] = 'questionnaire.' + cid
            label_names = {'parentSuite', 'suite', 'subSuite', 'change', 'feature', 'currentStatus', 'baselineStatus'}
            result['labels'] = [x for x in result['labels'] if x['name'] not in label_names]
            result['labels'] += [{'name': k, 'value': str(v)} for k, v in {
                'parentSuite': '第一阶段回归' if specs[cid]['phase'] == 1 else '第二阶段增量',
                'suite': specs[cid]['module'], 'subSuite': 'API自动化', 'change': row['change'],
                'feature': row['change'], 'currentStatus': row['status'], 'baselineStatus': row['baseline'].get('status', '无基线')}.items()]
            attachment = uuid.uuid4().hex + '-attachment.json'
            (results / attachment).write_bytes(encoded(row))
            result.setdefault('attachments', []).append({'name': '本轮结果与历史比较', 'source': attachment, 'type': 'application/json'})
            if row['status'] != 'passed':
                result.setdefault('statusDetails', {})['message'] = '[' + row['change'] + '] ' + row['actual']
        else:
            result = make_allure(specs[cid], row, run, lambda n, data: (results / n).write_bytes(data), directory)
            result['labels'].append({'name': 'feature', 'value': row['change']})
        (results / (result['uuid'] + '-result.json')).write_bytes(encoded(result))
    (results / 'categories.json').write_bytes(encoded([
        {'name': change, 'matchedStatuses': ['failed', 'broken'], 'messageRegex': '(?s).*\\[' + change + '\\].*'}
        for change in ('新增失败', '原有失败', '阻塞')]))
    (results / 'environment.properties').write_text('\n'.join([
        'Cycle=' + run['id'], 'ImplementationSHA256=' + run['implementationSha256'],
        'ManualPending=' + str(sum(r['execution'] == 'manual' and r['status'] == 'not_run' for r in summary['results'])),
        'ScriptsPending=' + str(sum(c['automation']['state']=='not_implemented' for c in specs.values())),
        'Baseline=Comparison only - no inherited passes',
        'Complete=' + str(summary['summary']['complete'])]) + '\n', 'utf-8')
    (results / 'executor.json').write_bytes(encoded({'name': '本轮API＋人工证据', 'type': 'local',
        'buildName': run['name'], 'reportName': ('完整执行' if summary['summary']['complete'] else '尚未完成全部测试') + ' / ' + run['id']}))
    lines = ['# 新一轮实际结果与基线对比', '', '历史结果仅比较；未执行、未编写与阻塞均不计通过。', '',
             '```json', json.dumps(summary['summary'], ensure_ascii=False, indent=2), '```', '']
    for change in ('新增失败', '原有失败', '已修复项', '跳过项', '阻塞', '未执行', '待编写脚本'):
        selected = [r for r in summary['results'] if r['change'] == change]
        lines += ['## ' + change + '（' + str(len(selected)) + '）', '']
        lines += ['- ' + r['caseId'] + ' ' + r['title'] for r in selected] or ['无。']
        lines.append('')
    lines += ['基线保留原有结果，但同ID本轮通过仅说明当前用例条件通过；不等于未经人工执行的页面问题已修复。',
              '', 'API证据：父目录下phase1/phase2的JUnit、pytest.log、observations.json及Allure附件。']
    (folder / 'comparison.md').write_text('\n'.join(lines), 'utf-8')
    if allure_cli:
        proc = subprocess.run([allure_cli, 'generate', str(results), '-o', str(folder / 'allure-report')])
        if proc.returncode:
            raise ValueError('Allure生成失败，原始结果已保留：' + str(folder))
    print('COMBINED:', folder, flush=True)
    print(json.dumps(summary['summary'], ensure_ascii=False), flush=True)
    if api_folder.resolve().is_relative_to(ROOT / 'test/执行记录/完整系统回归/本地运行'):
        receipt = STAGE / '执行记录/完整系统回归/最新合并报告.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temp = receipt.with_name(receipt.stem + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_bytes(encoded({'manualRunId': run['id'], 'apiDirectory': api_folder.relative_to(ROOT).as_posix(),
            'combinedDirectory': folder.relative_to(ROOT).as_posix(),
            'reportDirectory': (folder / 'allure-report').relative_to(ROOT).as_posix() if allure_cli else None,
            'summary': summary['summary'], 'generatedAt': datetime.now(timezone.utc).isoformat(),
            'manualEvidenceOrigin': run.get('importedManual')}))
        os.replace(temp, receipt)
    return folder, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--manual-run', help='收集页显示的32位批次ID')
    source.add_argument('--manual-zip', type=Path, help='用户导出的JSON＋截图ZIP；校验规格和业务实现后导入22项')
    p.add_argument('--manual-allure', type=Path, help='可选：人工Allure ZIP，与人工JSON/截图交叉核对')
    p.add_argument('--data-dir', type=Path, default=STORAGE)
    p.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018/?replicaSet=survey-rs')
    p.add_argument('--merge-only', type=Path, help='复用同批次本轮API，手工补录后生成一个新的汇总目录')
    p.add_argument('--allure', help='Allure CLI绝对路径；不填时只导出原始结果')
    args = p.parse_args()
    try:
        if args.manual_zip:
            if args.merge_only:
                raise ValueError('--merge-only需使用已导入的--manual-run批次ID')
            from .import_manual import import_manual
            run, directory = import_manual(args.manual_zip, args.manual_allure, args.data_dir)
            print('IMPORTED MANUAL RUN: ' + run['id'], flush=True)
        else:
            if not re.fullmatch('[a-f0-9]{32}', args.manual_run):
                raise ValueError('批次ID格式不正确')
            directory = args.data_dir.resolve() / args.manual_run
            run = read(directory / 'run.json')
        check_run(run)
        api_folder = args.merge_only.resolve() if args.merge_only else run_apis(run, args.mongo_uri)
        # Reload atomic on-disk manual save after the APIs finish.
        run = read(directory / 'run.json')
        _, summary = merge(run, directory, api_folder, args.allure)
        return 1 if any(r['status'] in ('failed', 'broken', 'blocked') for r in summary['results']) else 0
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        p.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
