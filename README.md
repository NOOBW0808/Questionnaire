# MongoDB在线问卷系统：第一阶段基础与第二阶段扩展

Flask/PyMongo与MongoDB副本集实现的在线问卷系统。保留注册登录、问卷管理与发布、四题型、跳转校验、保存提交和单卷统计；新增独立题目、版本历史/恢复、题库、指定版本共享、使用摘要授权与跨本人问卷单题统计。

## 1. 仓库结构

```text
questionnaire/
├─ README.md、README_PHASE2.md
├─ SUBMISSION_MANIFEST.json       当前文件哈希、来源、原文保护及差异
├─ SUBMISSION_VALIDATION.json     本轮整理验证，区别于历史业务结果
├─ code/                         完整系统；不含本地环境、密钥与数据库
│  ├─ README.md、README_PHASE2.md、CONFIGURATION.md
│  ├─ app.py、survey_app/、static/、scripts/
│  └─ 数据库设计/                 原8集合schema与第二阶段独立运行规范
├─ documents/                    四类正式说明按阶段分别保存
│  ├─ API说明/、关键逻辑说明/
│  └─ 数据库设计/、系统说明/       每类含第一阶段/、第二阶段/
└─ test/
   ├─ README.md、requirements.txt、一键运行与查看.ps1
   ├─ 测试步骤/                 第一阶段、第二阶段
   ├─ 自动化脚本/               第一阶段、第二阶段
   ├─ 手动测试/                 网页、原始导出
   ├─ 执行记录/                 第一阶段历史、第二阶段、完整系统回归
   └─ 工具/                     运行、合并、查看及历史过程归档
```

| 目的 | 入口 |
|---|---|
| 启动完整系统、升级旧库 | [code/README_PHASE2](code/README_PHASE2.md) |
| 阅读功能与权限 | [系统说明](documents/系统说明/第二阶段/系统说明.md) |
| 审查模型、快照、索引与迁移 | [数据库设计](documents/数据库设计/第二阶段/数据库设计.md) |
| 调用原接口及新增接口 | [API说明](documents/API说明/第二阶段/API说明.md) |
| 审查版本、共享、事务和统计 | [关键逻辑说明](documents/关键逻辑说明/第二阶段/关键逻辑说明.md) |
| 查看两阶段用例、证据及复跑 | [测试README](test/README.md) |

## 2. 快速启动

需要Python 3.11+、MongoDB 7+可写副本集，无需Node或前端构建。在本仓库根目录打开PowerShell：

```powershell
python -m venv code/.venv
& './code/.venv/Scripts/python.exe' -m pip install -r code/requirements-lock.txt
if (-not (Test-Path -LiteralPath 'code/.env')) { Copy-Item -LiteralPath 'code/.env.example' -Destination 'code/.env' }
& './code/.venv/Scripts/python.exe' code/scripts/local_mongo.py start --port 27018 --replica-set survey-rs --mongod 'C:/Program Files/MongoDB/Server/8.0/bin/mongod.exe'
$env:MONGODB_DATABASE='questionnaire_phase2_demo'
& './code/.venv/Scripts/python.exe' code/app.py
```

mongod路径按本机实际修改；访问http://127.0.0.1:5000，空专用库首次自行注册账号。完整旧库只进入兼容模式，原功能可用，新功能须显式升级，不能清空旧库绕过。精确步骤见[运行升级说明](code/README_PHASE2.md)。

## 3. 两阶段测试与报告

```powershell
& './code/.venv/Scripts/python.exe' -m pip install -r test/requirements.txt
& './code/.venv/Scripts/python.exe' test/工具/run_api_tests.py --collect-only
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --collect-only
# 仅查看已提交的完整合并报告，不重新执行测试。
& './code/.venv/Scripts/python.exe' test/工具/view_allure.py --port 5083
```

浏览器访问http://127.0.0.1:5083，终端保持运行。直接查看无需MongoDB或Allure CLI；重新生成需pytest/Allure及Java。已提交来源批次为213项：209通过、3失败、1跳过，其中22项人工记录有47张截图，API安排191项。保留非法ID500、游标400/404差异及精确服务端时钟跳过。原第一阶段143项历史报告保留在test/执行记录/第一阶段历史；新回归不覆盖旧结果，也不把历史通过继承为本轮通过。本轮整理验证见[SUBMISSION_VALIDATION](SUBMISSION_VALIDATION.json)。

## 4. 提交与历史保留

code为完整系统；documents第一阶段四份正文恢复基线，仅适配移动后的链接；第二阶段系统说明、关键逻辑和数据库设计依据项目完成报告相应部分，沿题目使用过程解释变化，API说明单独提供接口查询。设计过程资料不放入documents；test原规格与执行证据保留各自结果。旧PACKAGE_MANIFEST、RUN_VERIFICATION和历史测试清单只描述各自原批次，当前文件以根SUBMISSION_MANIFEST为准；Git对象和提交历史未重写。

只提交本仓库code、test、documents和根说明/校验文件，排除.env、虚拟环境、密钥、数据库、备份、缓存和test/执行记录/完整系统回归/本地运行本机运行目录。提交命令及实际验证限制见[完整运行与交付](code/README_PHASE2.md)。本轮没有执行暂存、commit或push，也没有迁移实际旧业务库。
