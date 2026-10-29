import copy
import json
import time
import unicodedata
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from bson import ObjectId

from conftest import CASES
from support import PASSWORD, data, error, iso, parallel, reset_rates, rule
from test_submission_statistics import filled, metric


def token_client(ctx, source, token=None):
    client=ctx.client()
    client.session.cookies.set("survey_session", token or source.session.cookies.get("survey_session"),domain="127.0.0.1",path="/")
    client.csrf=source.csrf
    return client


@pytest.mark.parametrize("case_id", [f"TC07-A{i:02}" for i in range(1,26)])
def test_auth_permissions(ctx, case_id, monkeypatch):
    n=int(case_id[-2:])
    if n<=17:
        client=ctx.client()
        before=ctx.db.users.count_documents({})
        if n==1:
            name="user_"+uuid.uuid4().hex
            r=client.call("POST","/auth/register",{"username":name,"password":PASSWORD})
            result=data(r,201)
            assert result["username"]==name and "passwordHash" not in result and "password" not in result
            error(client.call("GET","/auth/me"),401)
            record=ctx.db.users.find_one({"_id":ObjectId(result["userId"])})
            assert record["passwordHash"].startswith("$argon2") and record["passwordHash"]!=PASSWORD
            assert isinstance(record["createdAt"],datetime)
            from survey_app.security import hasher
            assert hasher.verify(record["passwordHash"],PASSWORD)
            client.login(name)
            assert data(client.call("GET","/auth/me"))["user"]["userId"]==result["userId"]
            assert ctx.db.users.count_documents({})==before+1
        elif 2<=n<=8:
            payload=CASES[case_id]["input"]
            r=client.call("POST","/auth/register",payload)
            if n in (2,3,6):
                error(r,422,"FIELD_INVALID")
                assert ctx.db.users.count_documents({})==before
            else:
                result=data(r,201)
                assert result["username"]==payload["username"].strip()
                client.login(payload["username"])
        elif n==9:
            suffix=uuid.uuid4().hex[:12]
            original="Ａlice_"+suffix
            result=data(client.call("POST","/auth/register",{"username":original,"password":PASSWORD}),201)
            error(ctx.client().call("POST","/auth/register",{"username":"alice_"+suffix,"password":PASSWORD}),409,"USERNAME_EXISTS")
            client.login("  ALICE_"+suffix+"  ")
            assert client.uid==result["userId"]
            record=ctx.db.users.find_one({"_id":ObjectId(client.uid)})
            assert record["usernameKey"]==unicodedata.normalize("NFKC",original).casefold()
            assert ctx.db.users.count_documents({})==before+1
        elif n==10:
            before_sessions=ctx.db.sessions.count_documents({})
            for payload in CASES[case_id]["input"]["注册输入"]:
                error(ctx.client().call("POST","/auth/register",payload),422)
            for payload in CASES[case_id]["input"]["原始JSON请求"]:
                error(ctx.client().call("POST","/auth/register",raw=payload),422)
            assert ctx.db.users.count_documents({})==before
            assert ctx.db.sessions.count_documents({})==before_sessions
        elif 11<=n<=15:
            password=CASES[case_id]["input"]["password"]
            name="pw_"+uuid.uuid4().hex
            r=client.call("POST","/auth/register",{"username":name,"password":password})
            if n in (13,14):
                data(r,201);client.login(name,password)
            else:
                error(r,422,"PASSWORD_INVALID")
                assert ctx.db.users.count_documents({})==before
        elif n==16:
            password="  PaSs😀12  "
            user=ctx.user(password=password)
            failures=[]
            for value in ["PaSs😀12","  pass😀12  ","  PaSs😀13  "]:
                r=ctx.client().call("POST","/auth/login",{"username":user.username,"password":value})
                # Trimmed variant has 7 codepoints: the catalog's demand for 401 here
                # conflicts with the stated 8-codepoint input contract. See report evidence.
                if len(value)<8:
                    error(r,422,"PASSWORD_INVALID")
                else:
                    failures.append(error(r,401,"LOGIN_FAILED")["message"])
            missing=error(ctx.client().call("POST","/auth/login",{"username":"missing_"+uuid.uuid4().hex,"password":PASSWORD}),401,"LOGIN_FAILED")
            assert all(msg==missing["message"] for msg in failures)
            for payload in [{"username":user.username},{"username":user.username,"password":"tiny"}]:
                error(ctx.client().call("POST","/auth/login",payload),422)
            error(ctx.client().call("POST","/auth/login",raw="{"),400)
            # A legal-length trimmed password still fails authentication, not format validation.
            other=ctx.user(password="  PaSs😀123  ")
            error(ctx.client().call("POST","/auth/login",{"username":other.username,"password":"PaSs😀123"}),401,"LOGIN_FAILED")
            ctx.evidence("TC07-A16 规格输入长度说明",{"original":"PaSs😀12","codepoints":7,"formatStatus":422,
                "additionalLegalTrimmedExample":"PaSs😀123","codepointsLegal":8,"authenticationStatus":401})
        elif n==17:
            name="race_"+uuid.uuid4().hex
            def register():
                return ctx.client().call("POST","/auth/register",{"username":name,"password":PASSWORD})
            results=parallel(register,register)
            assert sorted(r.status_code for r in results)==[201,409]
            client.login(name,"WrongPass!",expected=401)
            client.login(name)
            assert ctx.db.users.count_documents({"username":name})==1
        return
    if n==18:
        user=ctx.user()
        old=token_client(ctx,user)
        r=user.login(user.username)
        cookie=r.headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Expires=" in cookie
        error(old.call("GET","/auth/me"),401)
        data(user.call("GET","/auth/me"))
        second=token_client(ctx,user)
        data(user.call("POST","/auth/logout",{}),204)
        error(second.call("GET","/auth/me"),401)
        user.login(user.username)
        ctx.db.sessions.update_many({"userId":ObjectId(user.uid)},{"$set":{"expiresAt":datetime.now(timezone.utc)-timedelta(seconds=10)}})
        error(user.call("GET","/auth/me"),401)
        with monkeypatch.context() as patch:
            patch.setitem(ctx.app.config,"COOKIE_SECURE",True)
            r=user.login(user.username)
            assert "Secure" in r.headers["Set-Cookie"]
        return
    if n==24:
        user=ctx.user();before=ctx.db.surveys.count_documents({})
        for headers,code in [({"X-CSRF-Token":None},"CSRF_INVALID"),({"X-CSRF-Token":"wrong"},"CSRF_INVALID"),
                             ({"Origin":"https://other.invalid"},"ORIGIN_DENIED"),({"Sec-Fetch-Site":"cross-site"},"ORIGIN_DENIED")]:
            error(user.call("POST","/surveys",{"title":"forbidden"},headers=headers),403,code)
        assert ctx.db.surveys.count_documents({})==before
        for path in ["/auth/register","/auth/login"]:
            error(ctx.client().call("POST",path,{"username":"x","password":PASSWORD},headers={"Origin":"https://other.invalid"}),403,"ORIGIN_DENIED")
        # Exercise unmodified thresholds; use invalid fields to avoid 30 Argon2 jobs.
        # Requests consume quota before validation; X-Forwarded-For must not bypass IP scope.
        for category,limit in [("register",10),("login",20)]:
            reset_rates(category)
            anon=ctx.client()
            for i in range(limit):
                error(anon.call("POST","/auth/"+category,{},headers={"X-Forwarded-For":f"198.51.100.{i+1}"}),422)
            result=anon.call("POST","/auth/"+category,{})
            error(result,429,"RATE_LIMITED")
            assert "Traceback" not in result.text and "passwordHash" not in result.text
        # Move only the monotonic rate clock, then verify a natural window rollover.
        import survey_app.security as security
        real_time=security.time
        class RateClock:
            @staticmethod
            def monotonic():
                return real_time.monotonic()+61
        with monkeypatch.context() as patch:
            patch.setattr(security,"time",RateClock)
            error(ctx.client().call("POST","/auth/register",{}),422)
            error(ctx.client().call("POST","/auth/login",{}),422)
        ctx.evidence("限流时钟准备",{"thresholds":{"register":10,"login":20},"window":60,"clockAdvance":61,
            "unchanged":"production rate_limit, unchanged thresholds and queues; only test clock advanced"})
        return
    s=ctx.form(anonymous=n in (22,23))
    u=ctx.user();a=filled(s,u)
    if n==19:
        anon=ctx.client();old=copy.deepcopy(a.view)
        qid=s.qs[0]["questionId"]
        requests=[("POST","/surveys",{"title":"x"}),("GET","/surveys",None),("GET",s.path,None),
            ("PATCH",s.path,{"title":"x"}),("POST",s.path+"/publish",{}),("POST",s.path+"/close",{}),
            ("POST",s.path+"/questions",{"type":"TEXT","title":"x"}),("PATCH",s.path+"/questions/"+qid,{"title":"x"}),
            ("DELETE",s.path+"/questions/"+qid,None),("PUT",s.path+"/question-order",{"questionIds":[q["questionId"] for q in s.qs]}),
            ("PUT",s.path+"/questions/"+qid+"/jump-rules",{"rules":[]}),
            ("GET","/public-surveys/"+s.code,None),("POST","/public-surveys/"+s.code+"/attempts",{}),
            ("GET",a.path,None),("PUT",a.path+"/answers",{"answers":[]}),("POST",a.path+"/submit",{}),
            ("GET",s.path+"/statistics",None),("GET",s.path+"/questions/"+qid+"/statistics",None),
            ("GET",s.path+"/questions/"+s.qs[2]["questionId"]+"/answers",None),("GET","/auth/me",None),("POST","/auth/logout",{})]
        for method,path,payload in requests:
            error(anon.call(method,path,payload,headers={"If-Match":s.etag,"Idempotency-Key":uuid.uuid4().hex}),401,"UNAUTHENTICATED")
        assert a.refresh()["answers"]==old["answers"]
        assert s.stats()["submittedCount"]==0
    elif n==20:
        other=ctx.user();original=copy.deepcopy(s.view);qid=s.qs[0]["questionId"]
        ops=[("GET",s.path,None),("PATCH",s.path,{"title":"x"}),("POST",s.path+"/questions",{"type":"TEXT","title":"x"}),
            ("PATCH",s.path+"/questions/"+qid,{"title":"x"}),("POST",s.path+"/publish",{}),("POST",s.path+"/close",{}),
            ("PUT",s.path+"/question-order",{"questionIds":[q["questionId"] for q in s.qs]}),
            ("PUT",s.path+"/questions/"+qid+"/jump-rules",{"rules":[]}),
            ("DELETE",s.path+"/questions/"+qid,None),("GET",s.path+"/statistics",None),
            ("GET",s.path+"/questions/"+qid+"/statistics",None),
            ("GET",s.path+"/questions/"+s.qs[2]["questionId"]+"/answers",None)]
        for method,path,payload in ops:
            error(other.call(method,path,payload,headers={"If-Match":s.etag}),404,"NOT_FOUND")
        assert s.id not in {r["surveyId"] for r in data(other.call("GET","/surveys"))["items"]}
        assert s.refresh()==original
    elif n==21:
        old=copy.deepcopy(a.view)
        for user in [ctx.owner,ctx.user()]:
            for method,suffix,payload in [("GET","",None),("PUT","/answers",{"answers":[]}),("POST","/submit",{})]:
                error(user.call(method,a.path+suffix,payload,headers={"If-Match":a.etag,"Idempotency-Key":uuid.uuid4().hex}),404)
        error(u.call("PUT",a.path+"/answers",{"answers":a.entries(),"userId":ctx.owner.uid},headers={"If-Match":a.etag}),422)
        assert a.refresh()["answers"]==old["answers"]
    elif n==22:
        a.submit();filled(s,u,"20").submit();filled(s,number="30").submit()
        assert s.stats()["submittedCount"]==2
        for i in [2,3]:
            page=s.answers(i,limit=1);assert page["nextCursor"]
            page2=s.answers(i,limit=1,cursor=page["nextCursor"])
            for item in page["items"]+page2["items"]:
                assert set(item)=={"publishedVersion","value","submittedAt","anonymous"} and item["anonymous"] is True
            import base64
            decoded=base64.urlsafe_b64decode(page["nextCursor"])
            assert b'participationId' not in decoded and b'"id"' not in decoded
        for record in ctx.db.responses.find({"surveyId":ObjectId(s.id)}):
            assert "respondentUserId" not in record and "participationId" in record
        for result in [s.stats(),s.stats("/questions/"+s.qs[2]["questionId"])]:
            text=json.dumps(result)
            assert all(key not in text for key in ["username","respondentUserId","participationId","submissionSequence","attemptId","responseId"])
    elif n==23:
        # Establish old anonymous attempt before V2, submit afterwards.
        s.patch({"anonymous":False});s.publish()
        newer=filled(s,number="20");newer.submit();a.submit()
        items=s.answers(2)["items"];assert len(items)==2
        old=next(x for x in items if x["publishedVersion"]==1)
        new=next(x for x in items if x["publishedVersion"]==2)
        assert old["anonymous"] is True and "respondent" not in old
        assert new["respondent"]=={"username":newer.client.username} and not new["anonymous"]
        assert all(k not in json.dumps(items).lower() for k in ["hash","session","csrf","token"])
    elif n==25:
        a.submit();filled(s,number="20").submit();filled(s,number="30").submit()
        page=s.answers(2,limit=1);cursor=page["nextCursor"]
        path=s.path+"/questions/"+s.qs[2]["questionId"]+"/answers"
        # Preserve the expected ownership check order. Perform independent cursor
        # checks before asserting this response so one defect does not hide them.
        foreign_response=ctx.user().call("GET",path,params={"cursor":cursor})
        for params in [{"cursor":cursor[:-8]+"abcdefgh"},{"cursor":cursor,"publishedVersion":1}]:
            error(ctx.owner.call("GET",path,params=params),400,"CURSOR_INVALID")
        error(ctx.owner.call("GET",s.path+"/questions/"+s.qs[3]["questionId"]+"/answers",params={"cursor":cursor}),400)
        other=ctx.form()
        error(ctx.owner.call("GET",other.path+"/questions/"+other.qs[2]["questionId"]+"/answers",params={"cursor":cursor}),400)
        nextpage=s.answers(2,limit=1,cursor=cursor)
        assert len(nextpage["items"])==1 and nextpage["items"][0]["respondent"]!=page["items"][0]["respondent"]
        error(foreign_response,404,"NOT_FOUND")


def database_now(ctx):
    value=ctx.db.command("hello")["localTime"]
    # PyMongo's command decoder can return naive UTC even with tz_aware=True.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def await_deadline(ctx, target):
    end=time.monotonic()+10
    while database_now(ctx)<target:
        assert time.monotonic()<end,"Database time did not reach deadline in 10 seconds"
        time.sleep(0.04)


@pytest.mark.parametrize("case_id", [f"TC08-A{i:02}" for i in range(1,9)])
def test_time_versions(ctx, case_id):
    n=int(case_id[-2:])
    if n==5:
        pytest.skip("精确 $$NOW == deadlineAt 需受控 MongoDB 服务端时钟；本环境未提供该能力。TC08-A04/A06覆盖真实截止前后；本例不以sleep伪造恰等。")
    s=ctx.form(publish=False)
    sid=ObjectId(s.id)
    if n==1:
        begin=database_now(ctx);s.publish();end=database_now(ctx)
        first=ctx.db.survey_versions.find_one({"surveyId":sid,"publishedVersion":1})
        assert isinstance(first["publishedAt"],datetime) and begin<=first["publishedAt"]<=end
        s.publish();s.close();s.publish()
        assert ctx.db.survey_versions.find_one({"_id":first["_id"]})==first
        s.patch({"title":"第二版"});s.publish()
        second=ctx.db.survey_versions.find_one({"surveyId":sid,"publishedVersion":2})
        assert second["publishedAt"]>=first["publishedAt"]
        bad=ctx.form(questions=[],publish=False);before=copy.deepcopy(bad.view)
        bad.publish(expected=422)
        assert bad.refresh()==before and ctx.db.survey_versions.count_documents({"surveyId":ObjectId(bad.id)})==0
        ctx.evidence("各版本发布时间（存储层核对，页面缺失仍保留手动失败）",[first["publishedAt"],second["publishedAt"]])
    elif n==2:
        s.patch({"deadlineAt":"2099-12-31T23:59:00+08:00"})
        assert s.view["deadlineAt"]=="2099-12-31T15:59:00.000Z"
        assert ctx.db.surveys.find_one({"_id":sid})["deadlineAt"]==datetime(2099,12,31,15,59,tzinfo=timezone.utc)
        old=copy.deepcopy(s.view)
        for value in ["2026-10-21T12:00:00","bad-date"]:
            s.patch({"deadlineAt":value},expected=422,code="DEADLINE_INVALID")
            assert s.refresh()==old
        s.patch({"deadlineAt":None});s.publish()
        assert s.refresh()["deadlineAt"] is None and s.view["effectiveState"]=="PUBLISHED"
    elif n==3:
        last=s.view["updatedAt"]
        operations=[lambda:s.edit(0,{"title":"更新题干"}),s.publish,s.close,s.publish]
        for operation in operations:
            time.sleep(0.012)
            begin=datetime.now(timezone.utc)
            operation()
            now=datetime.fromisoformat(s.view["updatedAt"].replace("Z","+00:00"))
            assert s.view["updatedAt"]>last and now>=begin-timedelta(milliseconds=1)
            last=s.view["updatedAt"]
        before=copy.deepcopy(s.view);stale=s.etag
        a=filled(s);a.submit()
        assert s.refresh()==before
        s.patch({"title":"成功更新"})
        current=copy.deepcopy(s.view)
        s.patch({"title":"冲突"},expected=409,headers={"If-Match":stale})
        assert s.refresh()==current
    elif n in (4,6):
        s.publish()
        first=filled(s);key=uuid.uuid4().hex;receipt=data(first.submit(key),201)
        a=filled(s)
        target=database_now(ctx)+timedelta(seconds=2)
        s.patch({"deadlineAt":target.isoformat()})
        s.public(a.client)
        await_deadline(ctx,target)
        if n==6:
            assert data(first.submit(key,expected=200))["responseId"]==receipt["responseId"]
        a.submit(expected=409,code="NOT_COLLECTING")
        a.save(expected=409,code="NOT_COLLECTING")
        s.attempt(a.client,expected=409)
        assert s.refresh()["effectiveState"]=="EXPIRED"
        assert s.stats()["submittedCount"]==1 and metric(s)["average"]=="18"
        assert ctx.db.responses.count_documents({"surveyId":sid})==1
        assert ctx.db.submission_keys.count_documents({"attemptId":ObjectId(a.id)})==0
    elif n==7:
        s.publish();a=filled(s);s.close()
        s.attempt(a.client,expected=409);a.save(expected=409);a.submit(expected=409)
        s.patch({"deadlineAt":None});a.submit(expected=409,code="NOT_COLLECTING")
        s.publish();a.save();a.submit()
        assert a.refresh()["publishedVersion"]==1
        other=ctx.form();b=filled(other)
        other.patch({"deadlineAt":iso(-10)})
        assert other.refresh()["effectiveState"]=="EXPIRED"
        other.patch({"deadlineAt":iso(3600)})
        assert other.refresh()["effectiveState"]=="PUBLISHED"
        b.save();b.submit()
    elif n==8:
        s.publish();old=s.attempt();old_definition=copy.deepcopy(old.qs)
        s.edit(3,{"title":"年龄V2","validation":{"minValue":"0","maxValue":"10","integerOnly":True}});s.publish()
        new=s.attempt()
        assert old.refresh()["definition"]["questions"]==old_definition
        old.save();old.submit()
        new.save(expected=422,code="NUMBER_RANGE")
        vals=new.entries();vals[3]["value"]="10"
        new.save(entries=vals);new.submit()
        assert metric(s,3,1)["average"]=="18" and metric(s,3,2)["average"]=="10"
        for a,version in [(old,1),(new,2)]:
            assert ctx.db.responses.find_one({"attemptId":ObjectId(a.id)})["publishedVersion"]==version
        # Existing bound definitions still obey current collection state.
        pending=s.attempt();s.close();pending.save(expected=409,code="NOT_COLLECTING")
