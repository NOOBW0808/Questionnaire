"""Temporary UI verification server on a new random DB; Ctrl+C removes only that DB."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[3] / "code"))
from werkzeug.serving import make_server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri',required=True)
    args=parser.parse_args()
    name='questionnaire_p2_ui_'+uuid.uuid4().hex
    os.environ['MONGODB_URI']=args.mongo_uri;os.environ['MONGODB_DATABASE']=name
    from cryptography.fernet import Fernet
    os.environ['CURSOR_KEY']=Fernet.generate_key().decode()
    from survey_app import create_app
    from conftest import Client,submit_answer
    app=create_app();server=make_server('127.0.0.1',0,app,threaded=True)
    url=f'http://127.0.0.1:{server.server_port}';app.config['APP_ORIGIN']=url
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        owner=Client(url);recipient=Client(url);r,_=owner.resource('年龄（页面验证）')
        owner.call('PUT',f'/question-resources/{r["resourceId"]}/versions/{r["questionVersionId"]}/grants',
            {'username':recipient.name},headers={'If-None-Match':'*'})
        for title in ('页面验证A','页面验证B'):
            s,tag=owner.survey(title);imp,tag=owner.import_to(s['surveyId'],tag,r)
            owner.call('POST','/surveys/'+s['surveyId']+'/publish',{},etag=tag)
            submit_answer(owner,s['publicCode'],[{'questionId':imp['questionId'],'value':'20'}])
        s,_=recipient.survey('共享选用验证')
        folder=Path(__file__).resolve().parents[3]/'.local';folder.mkdir(exist_ok=True)
        info={'url':url,'database':name,'owner':owner.name,'recipient':recipient.name,'password':'TestPass!2026'}
        (folder/'phase2-ui.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(info,ensure_ascii=False),flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown();server.server_close();thread.join(10)
        assert name.startswith('questionnaire_p2_ui_')
        app.extensions['database'].client.drop_database(name);app.extensions['database'].client.close()


if __name__=='__main__':main()
