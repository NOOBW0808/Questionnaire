"""Run phase-two tests in random isolated databases; preserve historical evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
BATCHES={'1':['test_resources.py'], '2':['test_sharing.py'], '3':['test_cross_statistics.py','test_statistics_edges.py'],
         'transactions':['test_transactions.py'], 'migration':['test_migration.py'],
         'extended':['test_extended_resources.py','test_extended_statistics.py','test_extended_guards.py','test_extended_migration.py'], 'all':[]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',choices=list(BATCHES),default='all')
    p.add_argument('--mongo-uri',default=os.getenv('MONGODB_URI','mongodb://127.0.0.1:27018/?replicaSet=survey-rs'))
    p.add_argument('--collect-only',action='store_true')
    args=p.parse_args();env=os.environ.copy()
    env.update(MONGODB_URI=args.mongo_uri,PYTHONIOENCODING='utf-8',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONPATH=str(ROOT.parent/"code"))
    env.pop('KEEP_TEST_DATABASE',None)
    paths=[str(ROOT/'自动化脚本/第二阶段'/x) for x in BATCHES[args.batch]] or [str(ROOT/'自动化脚本/第二阶段')]
    cmd=[sys.executable,'-X','utf8','-m','pytest','-c',str(ROOT/'工具/pytest.ini'),*paths,'-q','--tb=short']
    if args.collect_only:return subprocess.run(cmd+['--collect-only'],env=env,cwd=ROOT).returncode
    folder=ROOT/'执行记录/完整系统回归/本地运行'/('phase2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    cmd+=['--junitxml',str(folder/'junit.xml')]
    print('New isolated run:',folder,flush=True)
    with (folder/'pytest.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(cmd,env=env,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        for line in process.stdout:print(line,end='',flush=True);log.write(line)
        result=process.wait()
    (folder/'run-summary.json').write_text(json.dumps({'kind':'phase2-isolated-http-mongodb','batch':args.batch,
        'exitCode':result,'historicalEvidenceOverwritten':False,'businessDatabaseMigrated':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':raise SystemExit(main())
