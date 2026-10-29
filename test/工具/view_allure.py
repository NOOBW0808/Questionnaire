"""Serve the latest generated report over localhost; do not double-click HTML."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

from test_workbench.common import ROOT, STAGE, read


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=5083)
    p.add_argument('--no-open',action='store_true')
    args=p.parse_args()
    try:
        receipt=read(STAGE/'执行记录/完整系统回归/最新合并报告.json')
        name=receipt.get('reportDirectory')
        if not name:p.error('尚未生成HTML；运行API时提供 --allure CLI路径')
        directory=(ROOT/name).resolve()
        allowed = (ROOT/'test/执行记录/完整系统回归/本地运行', STAGE/'执行记录/完整系统回归/完整合并批次')
        if not any(directory.is_relative_to(p.resolve()) for p in allowed) or not (directory/'index.html').is_file():
            p.error('最新报告目录无效或不存在，请重新生成')
        server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(SimpleHTTPRequestHandler,directory=str(directory)))
    except OSError as exc:
        p.error(str(exc))
    url=f'http://127.0.0.1:{args.port}'
    print('Allure: '+url+'\n目录: '+str(directory)+'\nCtrl+C 停止',flush=True)
    if not args.no_open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
