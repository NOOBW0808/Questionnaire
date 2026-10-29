import copy
import uuid
from decimal import Decimal

import allure
import pytest
from bson import ObjectId

from support import data, error, parallel, rule


def filled(s, client=None, number="18", text="很好", options=None):
    a = s.attempt(client)
    a.save([a.qs[0]["options"][0]["optionId"], options or [a.qs[1]["options"][0]["optionId"]], text, number])
    return a


def metric(s, index=3, version=None):
    params = {} if version is None else {"publishedVersion": version}
    result = s.stats(**params)
    v = result["versions"][0]
    return v["questions"][index]


def totals(ctx, s):
    return {name: ctx.db[name].count_documents({"surveyId": ObjectId(s.id)})
            for name in ("responses", "participation_guards")}


@pytest.mark.parametrize("case_id", [f"TC05-A{i:02}" for i in range(1, 14)])
def test_submission(ctx, case_id, monkeypatch):
    n = int(case_id[-2:])
    s = ctx.form(allowRepeat=n != 8)
    if n == 1:
        u, v = ctx.user(), ctx.user()
        a, b, c = s.attempt(u), s.attempt(u), s.attempt(v)
        assert len({a.id, b.id, c.id}) == 3
        stale = a.etag
        a.save(values=[a.qs[0]["options"][0]["optionId"]])
        b.save(values=[b.qs[0]["options"][1]["optionId"]])
        c.save(values=[c.qs[0]["options"][0]["optionId"]])
        original = copy.deepcopy(a.view)
        u.login(u.username)
        assert a.refresh()["answers"] == original["answers"]
        assert a.view["revision"] == 1 and b.refresh()["revision"] == 1
        a.save(values=[], expected=409, **{"If-Match": stale})
        assert a.refresh()["answers"] == original["answers"]
        assert all(ctx.db.attempts.find_one({"_id": ObjectId(x.id)})["publishedVersion"] == 1 for x in [a,b,c])
        return
    a = filled(s)
    if n == 2:
        key = uuid.uuid4().hex
        previous_revision = a.view["revision"]
        receipt = data(a.submit(key), 201)
        view = a.refresh()
        r = ctx.db.responses.find_one({"_id": ObjectId(receipt["responseId"])})
        g = ctx.db.participation_guards.find_one({"_id": r["participationId"]})
        k = ctx.db.submission_keys.find_one({"attemptId": ObjectId(a.id)})
        assert view["status"] == "SUBMITTED" and view["revision"] == previous_revision + 1
        assert view["responseId"] == receipt["responseId"] and view["submittedAt"] == receipt["submittedAt"]
        assert r["publishedVersion"] == view["publishedVersion"] == 1
        assert r["visitedQuestionIds"] == view["visitedQuestionIds"]
        from survey_app.common import plain
        assert plain(r["answers"]) == view["answers"]
        assert r["submissionSequence"] == g["acceptedCount"] == 1
        assert k["responseId"] == r["_id"] and k["attemptId"] == r["attemptId"]
        ctx.evidence("提交四集合一致性", {"response":r, "guard":g, "receipt":k})
        a.save(expected=409, code="ATTEMPT_SUBMITTED")
    elif 3 <= n <= 6:
        before = totals(ctx, s)
        h, status, code = {
            3: ({"If-Match": None}, 428, "PRECONDITION_REQUIRED"),
            4: ({"Idempotency-Key": None}, 400, "IDEMPOTENCY_REQUIRED"),
            5: ({"Idempotency-Key": "short"}, 400, "IDEMPOTENCY_REQUIRED"),
            6: ({"If-Match": '"attempt-0"'}, 409, "VERSION_CONFLICT")}[n]
        a.submit(expected=status, code=code, **h)
        assert totals(ctx, s) == before
        assert ctx.db.submission_keys.count_documents({"attemptId": ObjectId(a.id)}) == 0
        assert a.refresh()["status"] == "IN_PROGRESS"
    elif n == 7:
        a.submit()
        b = s.attempt(a.client)
        b.save(values=[b.qs[0]["options"][0]["optionId"]])
        b.submit(expected=422, code="ANSWER_REQUIRED")
        b.save()
        s.close()
        b.submit(expected=409, code="NOT_COLLECTING")
        assert totals(ctx,s) == {"responses":1,"participation_guards":1}
        assert metric(s)["average"] == "18"
        assert ctx.db.submission_keys.count_documents({"attemptId": ObjectId(b.id)}) == 0
    elif n == 8:
        b = filled(s, a.client)
        a.submit()
        a.client.login(a.client.username)
        s.attempt(a.client, expected=409)
        b.submit(expected=409, code="ALREADY_SUBMITTED")
        s.patch({"title":"第二版"})
        s.publish()
        s.attempt(a.client, expected=409)
        assert s.public(a.client)["canStart"] is False
        assert s.public(a.client)["hasSubmitted"] is True
        assert totals(ctx,s)["responses"] == 1
        # Independently exercise racing two pre-created attempts with repeat disabled.
        other = ctx.user()
        x, y = filled(s, other), filled(s, other)
        statuses = parallel(lambda: x.client.call("POST", x.path+"/submit", {},headers={"If-Match":x.etag,"Idempotency-Key":uuid.uuid4().hex}),
                            lambda: y.client.call("POST", y.path+"/submit", {},headers={"If-Match":y.etag,"Idempotency-Key":uuid.uuid4().hex}))
        assert sorted(r.status_code for r in statuses) == [201,409]
        assert [r.json()["error"]["code"] for r in statuses if r.status_code==409] == ["ALREADY_SUBMITTED"]
    elif n == 9:
        a.submit()
        b = filled(s,a.client,"20"); b.submit()
        filled(s,a.client,"99")
        assert totals(ctx,s)["responses"] == 2
        assert s.stats()["submittedCount"] == 1 and metric(s)["average"] == "20"
        assert sorted(r["submissionSequence"] for r in ctx.db.responses.find({"surveyId":ObjectId(s.id)})) == [1,2]
    elif n == 10:
        key = uuid.uuid4().hex
        first = data(a.submit(key),201)
        replay = data(a.submit(key,expected=200))
        assert replay["responseId"] == first["responseId"] and replay["replayed"] is True
        b = filled(s,a.client)
        b.submit(key,expected=409,code="IDEMPOTENCY_CONFLICT")
        a.submit(key,expected=409,code="IDEMPOTENCY_CONFLICT",**{"If-Match":'"attempt-999"'})
        a.submit(expected=409,code="ATTEMPT_SUBMITTED")
        s.close()
        assert data(a.submit(key,expected=200))["responseId"] == first["responseId"]
        assert totals(ctx,s)["responses"] == 1
    elif n == 11:
        key = uuid.uuid4().hex
        def send():
            return a.client.call("POST",a.path+"/submit",{},headers={"If-Match":a.etag,"Idempotency-Key":key})
        results = parallel(send,send)
        assert sorted(r.status_code for r in results) == [200,201]
        assert len({r.json()["data"]["responseId"] for r in results}) == 1
        assert totals(ctx,s) == {"responses":1,"participation_guards":1}
        assert ctx.db.submission_keys.count_documents({"attemptId":ObjectId(a.id)}) == 1
        assert s.stats()["submittedCount"] == 1
    elif n == 12:
        b, c = filled(s,a.client,"20"), filled(s,number="30")
        results = parallel(lambda:a.submit(),lambda:b.submit(),lambda:c.submit())
        assert all(r.status_code == 201 for r in results)
        rows = list(ctx.db.responses.find({"surveyId":ObjectId(s.id)}))
        own = sorted([r for r in rows if r["respondentUserId"]==ObjectId(a.client.uid)],key=lambda r:r["submissionSequence"])
        assert [r["submissionSequence"] for r in own] == [1,2]
        latest_number = own[-1]["answers"][3]["value"].to_decimal()
        assert Decimal(metric(s)["average"]) == (latest_number+Decimal(30))/2
        assert len(rows)==3 and s.stats()["submittedCount"]==2
    elif n == 13:
        # White-box fault injection at the PyMongo driver boundary, real DB transactions.
        # Do not replace the transaction function, route, gate, or HTTP result.
        from pymongo.synchronous.collection import Collection
        from pymongo.synchronous.client_session import ClientSession
        from pymongo.errors import OperationFailure
        original_insert = Collection.insert_one
        key = uuid.uuid4().hex
        for point in ("responses","submission_keys"):
            def insert(collection, *args, **kwargs):
                if collection.database.name == ctx.db.name and collection.name == point:
                    raise OperationFailure("test-only abort after previous transaction writes", code=2)
                return original_insert(collection,*args,**kwargs)
            before = totals(ctx,s)
            with monkeypatch.context() as patch:
                patch.setattr(Collection,"insert_one",insert)
                a.submit(key,expected=503,code="DATABASE_UNAVAILABLE")
            assert totals(ctx,s)==before
            assert ctx.db.submission_keys.count_documents({"attemptId":ObjectId(a.id)})==0
            assert a.refresh()["status"]=="IN_PROGRESS"
        original_commit = ClientSession.commit_transaction
        commits = []
        def unknown_once(session, *args, **kwargs):
            result = original_commit(session,*args,**kwargs)
            commits.append({"sessionId":str(session.session_id),"transactionId":str(session._transaction_id)})
            if len(commits)==1:
                raise OperationFailure("test-only lost commit acknowledgement",code=91,
                    details={"errorLabels":["UnknownTransactionCommitResult"]})
            return result
        with monkeypatch.context() as patch:
            patch.setattr(ClientSession,"commit_transaction",unknown_once)
            first=data(a.submit(key),201)
        assert len(commits)==2 and commits[0]==commits[1]
        assert data(a.submit(key,expected=200))["responseId"]==first["responseId"]
        assert totals(ctx,s)=={"responses":1,"participation_guards":1}
        assert ctx.db.participation_guards.find_one({"surveyId":ObjectId(s.id)})["acceptedCount"]==1
        ctx.evidence("驱动层故障注入与同一事务commit重试", {"faults":["after guard/before response","after response/before receipt"],"commits":commits})


@pytest.mark.parametrize("case_id", [f"TC06-A{i:02}" for i in range(1, 9)])
def test_statistics(ctx, case_id):
    n = int(case_id[-2:])
    s = ctx.form(publish=False)
    if n == 1:
        assert s.stats()["versions"] == [] and s.stats()["submittedCount"] == 0
        s.publish()
        result = s.stats()
        assert result["submittedCount"] == 0
        assert all(q["reachedCount"]==q["answeredCount"]==0 for q in result["versions"][0]["questions"])
        assert metric(s)["average"] is None
        assert s.answers(2)["items"]==[]
        error(ctx.owner.call("GET",s.path+"/statistics",params={"publishedVersion":999}),404,"NOT_FOUND")
        return
    if n in (3,4):
        s.edit(3,{"required":False})
    if n == 4:
        s.rule(0,[rule("EQ",s.qs[0]["options"][1]["optionId"])])
    s.publish()
    if n == 2:
        users=[ctx.user() for _ in range(3)]
        opts=[x["optionId"] for x in s.qs[1]["options"]]
        for client,num,text,choices in [(users[0],"10","很好",opts[:1]),(users[1],"20","满意",opts[:2]),
                                         (users[2],"30","",opts[1:2]),(users[0],"40","新意见",opts[2:])]:
            filled(s,client,num,text,choices).submit()
        filled(s,users[1],"99")
        stats=s.stats(); qs=stats["versions"][0]["questions"]
        assert stats["submittedCount"]==3
        assert [o["count"] for o in qs[0]["options"]]==[3,0]
        assert [o["count"] for o in qs[1]["options"]]==[1,2,1]
        assert (qs[2]["reachedCount"],qs[2]["answeredCount"] )==(3,2)
        assert qs[3]["average"]=="30" and qs[3]["answeredCount"]==3
        assert {v["value"] for v in s.answers(2)["items"]}=={"满意","新意见"}
        for i,q in enumerate(qs):
            assert metric(s,i)==s.stats("/questions/"+q["questionId"])["versions"][0]["questions"][0]
    elif n in (3,4):
        u,v=ctx.user(),ctx.user()
        filled(s,u,"10","旧意见").submit(); filled(s,v,"20","新意见").submit()
        if n==3:
            filled(s,u,None,"").submit()
        else:
            a=s.attempt(u);a.save([s.qs[0]["options"][1]["optionId"]]);a.submit()
        stats=s.stats();qs=stats["versions"][0]["questions"]
        assert stats["submittedCount"]==2 and qs[3]["average"]=="20"
        assert qs[3]["reachedCount"]==(2 if n==3 else 1) and qs[3]["answeredCount"]==1
        assert qs[2]["answeredCount"]==1
        assert [v["value"] for v in s.answers(2)["items"]]==["新意见"]
        assert [v["value"] for v in s.answers(3)["items"]]==["20"]
        if n==4:
            assert [o["count"] for o in qs[0]["options"]]==[1,1]
    elif n==5:
        for values,expected,answered in [(["0.00005"],"0.0001",1),(["-0.00005"],"-0.0001",1),
                                         (["-1","1"],"0",2),([None,None],None,0),(["0.1","0.2"],"0.15",2)]:
            f=ctx.form(questions=[{"type":"NUMBER","title":"精确小数","required":False}])
            for value in values:
                a=f.attempt();a.save([value]);a.submit()
            row=metric(f,0)
            assert row["average"]==expected and row["answeredCount"]==answered
    elif n==6:
        u,v=ctx.user(),ctx.user()
        filled(s,u,"10").submit();filled(s,v,"20").submit()
        s.edit(3,{"title":"年龄V2"});s.publish();filled(s,u,"30").submit()
        assert s.stats()["submittedCount"]==2
        assert s.stats(publishedVersion=1)["submittedCount"]==s.stats(publishedVersion=2)["submittedCount"]==1
        assert metric(s,3,1)["title"]=="Q4" and metric(s,3,1)["average"]=="20"
        assert metric(s,3,2)["title"]=="年龄V2" and metric(s,3,2)["average"]=="30"
    elif n==7:
        a=filled(s,number="10");key=uuid.uuid4().hex;old=data(a.submit(key),201)
        filled(s,a.client,"20").submit()
        assert data(a.submit(key,expected=200))["responseId"]==old["responseId"]
        assert metric(s)["average"]=="20" and s.stats()["submittedCount"]==1
        assert ctx.db.participation_guards.find_one({"surveyId":ObjectId(s.id)})["acceptedCount"]==2
    elif n==8:
        for i in range(3):
            filled(s,number=str(i),text=f"意见{i}").submit()
        p=s.answers(2,limit=2); assert len(p["items"])==2 and p["nextCursor"]
        p2=s.answers(2,limit=2,cursor=p["nextCursor"])
        assert len(p2["items"])==1 and p2["nextCursor"] is None
        assert len({r["value"] for r in p["items"]+p2["items"]})==3
        answer_path=s.path+"/questions/"+s.qs[2]["questionId"]+"/answers"
        for limit in ["0","101","abc"]:
            error(ctx.owner.call("GET",answer_path,params={"limit":limit}),400,"PAGE_INVALID")
        for cursor in [p["nextCursor"][:-5]+"xxxxx","invalid"]:
            error(ctx.owner.call("GET",answer_path,params={"cursor":cursor}),400,"CURSOR_INVALID")
        error(ctx.owner.call("GET",s.path+"/questions/unknown/statistics"),404)
        error(ctx.owner.call("GET",s.path+"/questions/"+s.qs[3]["questionId"]+"/answers",params={"cursor":p["nextCursor"]}),400)
        for i in range(20):
            s.patch({"title":f"第{i+2}版"});s.publish()
        versions=s.stats();assert len(versions["versions"])==20 and versions["nextVersionCursor"]
        last=s.stats(versionCursor=versions["nextVersionCursor"])
        assert len(last["versions"])==1 and last["nextVersionCursor"] is None
        assert versions["submittedCount"]==last["submittedCount"]==3
        assert {v["publishedVersion"] for v in versions["versions"]+last["versions"]}==set(range(1,22))
        error(ctx.owner.call("GET",s.path+"/statistics",params={"versionCursor":versions["nextVersionCursor"],"publishedVersion":1}),400)
        assert metric(s,3,1)==s.stats("/questions/"+s.qs[3]["questionId"],publishedVersion=1)["versions"][0]["questions"][0]


def test_21_accounts_replaces_manual_workload(ctx):
    """Automates only count/data/page checks from TC06-M01, never UI readability."""
    allure.dynamic.title("TC06-M01-AUTO 21 个账号的统计与答案分页（补充）")
    allure.dynamic.suite("统计")
    allure.dynamic.sub_suite("API 自动化")
    s=ctx.form()
    people=[ctx.user() for _ in range(21)]
    for i,client in enumerate(people):
        filled(s,client,str(i),f"意见{i:02}").submit()
    filled(s,people[0],"42","最后意见").submit()
    assert s.stats()["submittedCount"]==21 and metric(s)["average"]=="12"
    assert totals(ctx,s)["responses"]==22
    rows=[];cursor=None;page_sizes=[]
    for _ in range(30):
        page=s.answers(2,limit=5,**({"cursor":cursor} if cursor else {}))
        rows.extend(page["items"]);page_sizes.append(len(page["items"]))
        cursor=page["nextCursor"]
        if cursor is None:
            break
    assert cursor is None and page_sizes==[5,5,5,5,1]
    assert len(rows)==len({r["value"] for r in rows})==21
    assert "意见00" not in {r["value"] for r in rows} and "最后意见" in {r["value"] for r in rows}
    ctx.evidence("TC06-M01 自动化覆盖边界", {"accounts":21,"historyResponses":22,"latestPeople":21,"answerPageSizes":page_sizes,
                                             "unverified":"页面可读性、图表及空均值显示需手动验证"})
