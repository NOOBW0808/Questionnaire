"""Tool checks only; temporary artificial screenshots never become business evidence."""
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from .collector import create_app
from .common import classification, read, CATALOG, STAGE, encoded, sha
from .exports import actual_results, result_zip, evidence_file


class WorkbenchChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phase2-collector-selfcheck-')
        self.storage = Path(self.temp.name)
        self.app = create_app(self.storage)
        self.client = self.app.test_client()
        boot = self.client.get('/api/bootstrap').get_json()
        self.headers = {'X-Collector-Token': boot['token'], 'Origin': 'http://localhost'}
        r = self.client.post('/api/runs', json={'name': '工具自检，非业务验收', 'tester': 'selfcheck',
            'targetUrl': 'http://127.0.0.1:5000', 'environment': 'temporary synthetic tool check'}, headers=self.headers)
        self.assertEqual(r.status_code, 201)
        self.run = r.get_json()
        self.cid = 'TC09-M01'
        self.url = '/api/runs/' + self.run['id']

    def tearDown(self):
        self.temp.cleanup()

    def upload(self):
        img = io.BytesIO()
        Image.new('RGB', (64, 32), 'white').save(img, 'PNG')
        img.seek(0)
        r = self.client.post(self.url + '/cases/' + self.cid + '/attachments',
            data={'revision': str(self.run['revision']), 'file': (img, '自检.png')}, headers=self.headers)
        self.assertEqual(r.status_code, 201)
        self.run = r.get_json()

    def save(self, status='passed', actual='工具自检的人工文字，非业务结果', revision=None):
        r = self.client.put(self.url + '/cases/' + self.cid,
            json={'revision': self.run['revision'] if revision is None else revision, 'status': status, 'actual': actual}, headers=self.headers)
        if r.status_code == 200:
            self.run = r.get_json()
        return r

    def test_complete_specs_and_counts(self):
        cases = read(CATALOG)['cases']
        self.assertEqual(len(cases), 213)
        self.assertEqual(sum(c['execution'] == 'manual' for c in cases), 22)
        self.assertEqual(sum(c['execution'] == 'api' and c['automation']['state'] == 'implemented' for c in cases), 191)
        self.assertEqual(sum(c['automation']['state'] == 'not_implemented' for c in cases), 0)
        for c in cases:
            self.assertTrue(c['preconditions'] and c['steps'] and c['expected'])
            self.assertIsInstance(c['input'], dict)
            self.assertTrue(all(s['action'] and s['expected'] for s in c['steps']))

    def test_fresh_run_inherits_no_passes(self):
        result = actual_results(self.run)
        self.assertEqual(result['summary']['statuses'], {'not_run': 213})
        self.assertEqual(result['summary']['apiExecuted'], 0)
        self.assertFalse(result['summary']['allPassed'])
        self.assertEqual(self.run['baseline']['cases']['TC07-A25']['status'], 'failed')
        self.assertEqual(self.run['results'], {})

    def test_pass_and_fail_need_screenshot_and_observation(self):
        self.assertEqual(self.save().status_code, 400)
        self.upload()
        self.assertEqual(self.save(actual='').status_code, 400)
        self.assertEqual(self.save(status='failed').status_code, 200)
        self.assertEqual(self.save().status_code, 200)
        reload = self.client.get(self.url).get_json()
        self.assertEqual(reload['results'][self.cid]['status'], 'passed')

    def test_cas_preserves_saved_result(self):
        self.upload()
        stale = self.run['revision']
        self.assertEqual(self.save(status='failed').status_code, 200)
        self.assertEqual(self.save(revision=stale).status_code, 409)
        self.assertEqual(self.client.get(self.url).get_json()['results'][self.cid]['status'], 'failed')

    def test_api_cannot_be_manual_marked(self):
        self.cid = 'TC09-A01'
        self.assertEqual(self.save(status='skipped').status_code, 404)

    def test_reject_non_image(self):
        r = self.client.post(self.url + '/cases/' + self.cid + '/attachments',
            data={'revision': '0', 'file': (io.BytesIO(b'not a png'), 'x.png')}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_evidence_cannot_disappear_after_pass(self):
        self.upload()
        self.save()
        item = self.run['results'][self.cid]['attachments'][0]
        r = self.client.delete(self.url + '/cases/' + self.cid + '/attachments/' + item['id'],
            headers={**self.headers, 'X-Run-Revision': str(self.run['revision'])})
        self.assertEqual(r.status_code, 400)
        directory = self.storage / self.run['id']
        (directory / item['path']).write_bytes(b'changed')
        self.assertEqual(self.client.get(self.url + '/export/bundle').status_code, 400)

    def test_exports_portable_and_only_manual_allure(self):
        self.upload()
        self.save()
        r = self.client.get(self.url + '/export/bundle')
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.data)) as archive:
            self.assertIn('actual-results.json', archive.namelist())
            self.assertEqual(len([x for x in archive.namelist() if x.startswith('attachments/')]), 1)
        r = self.client.get(self.url + '/export/allure')
        with zipfile.ZipFile(io.BytesIO(r.data)) as archive:
            names = [x for x in archive.namelist() if x.endswith('-result.json')]
            self.assertEqual(len(names), 22)
            import json
            results = [json.loads(archive.read(n)) for n in names]
            self.assertEqual(sum(x['status'] == 'passed' for x in results), 1)
            self.assertEqual(sum(x['status'] == 'skipped' for x in results), 21)

    def test_write_token_origin_and_host(self):
        self.assertEqual(self.client.post('/api/runs', json={}).status_code, 403)
        self.assertEqual(self.client.put(self.url + '/cases/' + self.cid, json={},
            headers={**self.headers, 'Origin': 'http://evil.test'}).status_code, 403)
        self.assertEqual(self.client.get('/api/bootstrap', headers={'Host': 'evil.test'}).status_code, 403)

    def test_change_classifications(self):
        for current, before, expected in [
            ('failed', 'passed', '新增失败'), ('failed', 'failed', '原有失败'),
            ('passed', 'failed', '已修复项'), ('skipped', 'failed', '跳过项'),
            ('not_run', 'passed', '未执行'), ('not_implemented', None, '待编写脚本'),
            ('blocked', 'passed', '阻塞')]:
            self.assertEqual(classification(current, before), expected)

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            evidence_file(self.storage, {'path': '../x', 'name': 'x', 'sha256': '0'})

    def mock_api_cycle(self):
        """Temporary fake data only tests the exporter, never the survey system."""
        folder = self.storage / 'synthetic-cycle'
        folder.mkdir()
        manifest = {k: self.run[k] for k in ('id', 'catalogSha256', 'baselineSha256', 'implementationSha256', 'testSuiteSha256')}
        manifest.update(kind='fresh-api-cycle', batches={'phase1': {}, 'phase2': {}})
        for phase in ('phase1', 'phase2'):
            raw = folder / phase / 'allure-results'
            raw.mkdir(parents=True)
            rows = {}
            for c in self.run['catalog']['cases']:
                if c['execution'] != 'api' or c['automation']['state'] != 'implemented' or ('phase' + str(c['phase'])) != phase:
                    continue
                cid = c['id']
                status = 'passed'
                # One new failure, one existing failure, one repaired item and a skip.
                if cid == 'TC01-A01':
                    status = 'failed'
                if cid == 'TC07-A25':
                    status = 'passed'
                if cid == 'TC08-A05':
                    status = 'skipped'
                rows[cid] = dict(caseId=cid, status=status, actual='SYNTHETIC TOOL CHECK',
                                 startedAt=self.run['createdAt'], updatedAt=self.run['createdAt'], reports=[])
                (raw / (cid + '-result.json')).write_bytes(encoded({'uuid': cid, 'status': status, 'labels': [
                    {'name': 'caseId', 'value': cid}, {'name': 'cycleId', 'value': self.run['id']}]}))
            (folder / phase / 'observations.json').write_bytes(encoded({'cycleId': self.run['id'], 'results': rows}))
        manifest['evidenceSha256'] = {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file()}
        (folder / 'cycle.json').write_bytes(encoded(manifest))
        return folder

    def test_merge_current_only_and_comparison_labels(self):
        from .cycle import merge
        folder = self.mock_api_cycle()
        # Retest a historical manual failure as failed, with a synthetic tool image.
        self.cid = 'TC01-M02'
        self.upload()
        self.save(status='failed')
        output, summary = merge(self.run, self.storage / self.run['id'], folder)
        rows = {r['caseId']: r for r in summary['results']}
        self.assertEqual(rows['TC01-A01']['change'], '新增失败')
        self.assertEqual(rows['TC01-M02']['change'], '原有失败')
        self.assertEqual(rows['TC07-A25']['change'], '已修复项')
        self.assertEqual(rows['TC08-A05']['change'], '跳过项')
        self.assertEqual(len(list((output / 'allure-results').glob('*-result.json'))), 213)
        self.assertFalse(summary['summary']['complete'])

    def test_merge_rejects_wrong_cycle_or_changed_evidence(self):
        from .cycle import load_api
        folder = self.mock_api_cycle()
        wrong = deepcopy(self.run)
        wrong['id'] = '0' * 32
        with self.assertRaises(ValueError):
            load_api(folder, wrong)
        (folder / 'phase1/observations.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            load_api(folder, self.run)

    def test_changed_code_stamp_blocks_mixing(self):
        from .cycle import check_run
        wrong = deepcopy(self.run)
        wrong['implementationSha256'] = '0' * 64
        with self.assertRaises(ValueError):
            check_run(wrong)

    def test_user_archives_import_22_unchanged_manual_records(self):
        from .import_manual import import_manual, REMOVED_MANUAL
        run, directory = import_manual(STAGE/'手动测试/原始导出/manual-results.zip', STAGE/'手动测试/原始导出/allure-results.zip', self.storage)
        self.assertEqual(len(run['results']),22)
        self.assertEqual(run['importedManual']['screenshots'],47)
        self.assertTrue(all(r['status']=='passed' for r in run['results'].values()))
        self.assertFalse(set(run['results'])&set(REMOVED_MANUAL))
        self.assertFalse(run['importedManual']['manualReexecuted'])
        self.assertEqual(len(run['importedManual']['excludedCases']),7)
        exported=actual_results(run)
        self.assertEqual(exported['summary']['statuses'],{'passed':22,'not_run':191})
        self.assertEqual(exported['summary']['changes']['已修复项'],3)
        with zipfile.ZipFile(STAGE/'手动测试/原始导出/manual-results.zip') as z:
            import json
            source={x['caseId']:x for x in json.loads(z.read('actual-results.json'))['results']}
            for cid,row in run['results'].items():
                self.assertEqual(row['actual'],source[cid]['actual'])
                for item in row['attachments']:
                    self.assertEqual((directory/item['path']).read_bytes(),z.read(item['path']))

    def test_import_rejects_tampered_screenshot(self):
        from .import_manual import import_manual
        corrupted=self.storage/'corrupted.zip'
        with zipfile.ZipFile(STAGE/'手动测试/原始导出/manual-results.zip') as source, zipfile.ZipFile(corrupted,'w') as dest:
            image=next(n for n in source.namelist() if n.startswith('attachments/'))
            for name in source.namelist():
                dest.writestr(name,b'changed' if name==image else source.read(name))
        with self.assertRaisesRegex(ValueError,'截图'):
            import_manual(corrupted,storage=self.storage)

    def test_import_rejects_zip_traversal(self):
        from .import_manual import archive
        corrupted=self.storage/'traversal.zip'
        with zipfile.ZipFile(corrupted,'w') as dest:dest.writestr('../outside','x')
        with self.assertRaisesRegex(ValueError,'路径'):
            archive(corrupted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
