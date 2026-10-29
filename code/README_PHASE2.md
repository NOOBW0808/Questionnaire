# 完整系统运行、升级与交付

本文件用于GitHub组合仓库，命令除特别说明外从仓库根目录执行；原第一阶段说明保留在code/README.md，以下为完整系统的当前操作。第二阶段在原Flask/PyMongo结构上扩展，业务代码与根目录已确认实现逐文件相同。实际旧业务库未迁移，本轮未提交或push。

## 1. 安装与启动

Python 3.11+、MongoDB 7+可写副本集；无需Node或前端构建。使用同一个解释器创建并安装虚拟环境。

```powershell
python -m venv code/.venv
& './code/.venv/Scripts/python.exe' -m pip install -r code/requirements-lock.txt
& './code/.venv/Scripts/python.exe' -c 'from argon2 import PasswordHasher; import pymongo; print("依赖导入正常")'
if (-not (Test-Path -LiteralPath 'code/.env')) { Copy-Item -LiteralPath 'code/.env.example' -Destination 'code/.env' }
& './code/.venv/Scripts/python.exe' code/scripts/local_mongo.py start --port 27018 --replica-set survey-rs --mongod 'C:/Program Files/MongoDB/Server/8.0/bin/mongod.exe'
# 首次演示使用未使用过的专用库；这不代表旧库迁移。
$env:MONGODB_DATABASE='questionnaire_phase2_demo'
$env:MONGODB_URI='mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
$env:APP_ORIGIN='http://127.0.0.1:5000'
& './code/.venv/Scripts/python.exe' code/app.py
```

访问http://127.0.0.1:5000，首次新库自行注册账号。mongod路径按本机实际修改，若端口已占用须确认是哪一个实例，不另开同端口实例。也可进入code目录用其中虚拟环境运行app.py。`.env`和密钥始终留本地。

| 实际结构 | 行为 |
|---|---|
| 真空专用库 | 初始化18集合及索引。 |
| 完整第一阶段8集合、responses为v2 | 原功能继续，新功能提示显式升级。 |
| 完整目标并激活或新库 | 启用第二阶段。 |
| 漂移、未知集合、中间态 | 明确阻断，不覆盖或删除历史。 |

## 2. 旧库预检、备份及恢复副本

以下为操作步骤，不代表已经迁移实际旧业务库。正式操作前停止所有应用写入；--maintenance只是操作者声明，工具不代替停写。先在恢复副本演练并审查历史内容。

```powershell
$phase2Uri='mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
$phase2Db='替换为已核实的旧库名'
$phase2Backup='./.local/phase2-backup-review'
$phase2Manifest="$phase2Backup/manifest.json"
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py preflight --uri $phase2Uri --database $phase2Db
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py plan --uri $phase2Uri --database $phase2Db
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py backup --uri $phase2Uri --database $phase2Db --maintenance --output $phase2Backup
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py restore-copy --uri $phase2Uri --database $phase2Db --maintenance --backup $phase2Manifest --target-database 'questionnaire_phase2_restore_review'
```

每步检查$LASTEXITCODE，非零停止；备份目录及恢复目标均须未存在。备份包含BSON、DDL/索引、源目标规范和哈希，整目录保留，禁止push。TTL删除也会改变旧基线，遇到变化应重新审查稳定窗口，不能跳过校验。

## 3. 明确升级、验证、激活及恢复

```powershell
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py apply --uri $phase2Uri --database $phase2Db --maintenance --backup $phase2Manifest
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py verify --uri $phase2Uri --database $phase2Db --maintenance --backup $phase2Manifest
# 审查通过以后才激活，再恢复应用写入。
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py activate --uri $phase2Uri --database $phase2Db --maintenance --backup $phase2Manifest
```

默认新增十集合/索引、扩展两个validator，不批量改问卷/快照/attempt/response，不根据题干推断来源。真实绑定投影按唯一键、来源哈希和migration_items续跑。DDL非整体事务，中断保留台账，用同一计划及备份续跑。源/备份/索引冲突停止。

```powershell
# 仅未激活、旧基线未变、新业务/投影为空且结构可证明时允许。
& './code/.venv/Scripts/python.exe' code/scripts/phase2_schema.py rollback --uri $phase2Uri --database $phase2Db --maintenance --backup $phase2Manifest
```

回退先归档台账，只清理本计划新增的空结构和恢复原validator，不能删旧集合。已激活或已有新数据时拒绝自动降级，保全数据后单独审查。responses v1、孤儿引用、未知schema、无证据ADOPT及不同单位合并不能直接兼容，不删数据解决。

## 4. 操作入口及测试

我的题目/版本历史 → 编辑器选用、ADOPT、换版/派生 → 题库及共享给我 → 摘要授权/使用查询 → 跨卷统计。资源更新不会自动改实例或旧发布；跨用户导入先确认SUMMARY_V1；答案统计只汇总本人问卷。完整功能、请求和数据边界见[文档导航](../documents/README.md)。

```powershell
& './code/.venv/Scripts/python.exe' -m pip install -r test/requirements.txt
& './code/.venv/Scripts/python.exe' test/工具/run_api_tests.py --collect-only
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --collect-only
& './code/.venv/Scripts/python.exe' test/工具/run_api_tests.py --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
```

第一阶段130 API、第二阶段61 API。仅操作随机隔离库，新结果进test/执行记录/完整系统回归/本地运行，原始证据不覆盖。历史完整合并记录为213项、209通过/3失败/1跳过（22人工+191 API安排）；当前不宣称全部通过。目录整理后的验证与该历史批次分开记录。手工证据导入、报告查看及已知缺陷见[test/README](../test/README.md)。

## 5. GitHub提交范围

提交code、test、documents、根README、README_PHASE2、SUBMISSION_MANIFEST及忽略规则；不提交.env、虚拟环境、MongoDB数据、密钥、备份、缓存、新运行目录及仓库外开发副本。代码内PACKAGE_MANIFEST和RUN_VERIFICATION是历史批次，不是本轮全量清单。

```powershell
git status --short
git diff --stat
git add code test documents README.md README_PHASE2.md SUBMISSION_MANIFEST.json SUBMISSION_VALIDATION.json .gitignore .gitattributes
git diff --cached --stat
git commit -m "Extend questionnaire with reusable questions and phase-two tests"
git push
```

这些命令供检查后自行执行，本轮不自动暂存、提交或推送。
