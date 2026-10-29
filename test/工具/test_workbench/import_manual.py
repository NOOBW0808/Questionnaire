"""Import user-provided manual evidence; compare unchanged specs/code explicitly."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid
import zipfile

from .common import BASELINE, CATALOG, STORAGE, encoded, implementation_hash, read, sha, test_suite_hash

REMOVED_MANUAL = ('TC14-M02','TC15-M01','TC16-M01','TC20-M01','TC20-M02','TC20-M03','TC20-M04')


def archive(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    if len(names) != len(set(names)) or sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
        z.close()
        raise ValueError('证据ZIP含重复路径或展开体积超出256MiB')
    for name in names:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
            z.close()
            raise ValueError('证据ZIP含无效路径')
    return z


def import_manual(bundle, allure_bundle=None, storage=STORAGE):
    catalog = read(CATALOG)
    with archive(bundle) as z:
        doc = json.loads(z.read('actual-results.json'))
        source_catalog = json.loads(z.read('cases.json'))
        if doc['run']['implementationSha256'] != implementation_hash():
            raise ValueError('人工测试时的业务实现与当前代码不同；须人工确认/重测，不能自动复用')
        source_specs = {c['id']: c for c in source_catalog['cases']}
        rows = {r['caseId']: r for r in doc['results'] if r['execution'] == 'manual'}
        if len(rows) != len([r for r in doc['results'] if r['execution']=='manual']):
            raise ValueError('人工结果包含重复caseId')
        required = {c['id']: c for c in catalog['cases'] if c['execution']=='manual'}
        extras = set(rows) - set(required)
        if not set(required) <= set(rows) or extras not in (set(), set(REMOVED_MANUAL)):
            raise ValueError('来源手工用例与当前范围不符；仅允许明确移除的末七项')
        for cid in extras:
            if rows[cid]['status'] != 'skipped':
                raise ValueError('被移除项不是用户说明的跳过状态：'+cid)
        results, images = {}, {}
        for cid, case in required.items():
            if source_specs.get(cid) != case:
                raise ValueError('人工用例规格发生变化，不能直接沿用：'+cid)
            row = rows[cid]
            if row['status'] not in ('passed','failed','blocked','skipped','not_run'):
                raise ValueError('人工状态非法：'+cid)
            if row['status'] != 'not_run' and not row['actual'].strip():
                raise ValueError('缺少实际现象：'+cid)
            if row['status'] in ('passed','failed') and not row['attachments']:
                raise ValueError('缺少截图：'+cid)
            for item in row['attachments']:
                p = PurePosixPath(item['path'])
                if p.parts[0] != 'attachments' or len(p.parts)!=2:
                    raise ValueError('截图路径异常：'+cid)
                data=z.read(item['path'])
                if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
                    raise ValueError('截图内容或哈希异常：'+cid)
                if item['path'] in images and images[item['path']]!=data:
                    raise ValueError('截图路径冲突')
                images[item['path']]=data
            results[cid]={k:deepcopy(row.get(k)) for k in ('status','actual','startedAt','updatedAt','attachments')}
            results[cid]['evidenceOrigin']={'kind':'user-recorded-manual-import','sourceRunId':doc['run']['id'],
                                          'recordedAt':row.get('updatedAt'),'reexecutedByAutomation':False}
    if allure_bundle:
        with archive(allure_bundle) as z:
            exported={}
            for name in z.namelist():
                if not name.endswith('-result.json'):continue
                result=json.loads(z.read(name))
                ids={x['value'] for x in result['labels'] if x['name']=='caseId'}
                if len(ids)!=1:raise ValueError('人工Allure缺少唯一caseId')
                cid=ids.pop()
                if cid in exported:raise ValueError('人工Allure重复caseId')
                exported[cid]=result
            if set(exported)!=set(rows):raise ValueError('人工ZIP与Allure的用例集合不同')
            for cid,row in rows.items():
                result=exported[cid]
                expected=row['status'] if row['status'] in ('passed','failed') else 'broken' if row['status']=='blocked' else 'skipped'
                if result['status']!=expected:raise ValueError('两份导出包状态不一致：'+cid)
                actual=[];hashes=[]
                for a in result.get('attachments',[]):
                    data=z.read(a['source'])
                    if a['type'].startswith('image/'):
                        hashes.append(hashlib.sha256(data).hexdigest())
                    elif a['name']=='实际结果':actual.append(json.loads(data))
                    elif a['name']=='本轮实际结果与基线对比':actual.append(json.loads(data))
                if len(actual)!=1 or actual[0]['actual']!=row['actual']:
                    raise ValueError('两份导出包实际描述不一致：'+cid)
                if sorted(hashes)!=sorted(a['sha256'] for a in row['attachments']):
                    raise ValueError('两份导出包截图不一致：'+cid)
    now=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    run={'schemaVersion':2,'id':uuid.uuid4().hex,'name':'已完成手工22项＋新一轮业务API',
         'tester':'用户手工记录＋API脚本执行','targetUrl':doc['run']['targetUrl'],
         'environment':doc['run']['environment'],'revision':0,'createdAt':now,'updatedAt':now,
         'catalogSha256':sha(CATALOG),'baselineSha256':sha(BASELINE),'implementationSha256':implementation_hash(),
         'testSuiteSha256':test_suite_hash(),'catalog':catalog,'baseline':read(BASELINE),'results':results,
         'importedManual':{'sourceRun':doc['run'],'bundleSha256':sha(bundle),
             'allureBundleSha256':sha(allure_bundle) if allure_bundle else None,
             'specsUnchanged':True,'implementationUnchanged':True,'manualReexecuted':False,
             'excludedCases':[{'caseId':cid,'sourceStatus':rows[cid]['status'],'reason':'用户明确移除，非API代验通过'} for cid in REMOVED_MANUAL if cid in extras],
             'importedCaseIds':list(required),'importedAt':now,'screenshots':len(images)}}
    directory=Path(storage)/run['id']
    directory.mkdir(parents=True)
    (directory/'attachments').mkdir()
    for name,data in images.items():(directory/name).write_bytes(data)
    (directory/'run.json').write_bytes(encoded(run))
    return run,directory
