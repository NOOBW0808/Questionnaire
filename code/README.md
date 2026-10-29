> 完整系统入口：本文件保留第一阶段原说明。第二阶段完整运行与旧库升级以[README_PHASE2](README_PHASE2.md)为准；原八集合自动安装等表述属于第一阶段历史，不能用于已升级数据库。

# 在线问卷系统后端代码提交

本目录包含问卷系统后端、MongoDB schema 和运行配置说明。在本目录操作，可启动问卷 HTTP 服务并进行注册、登录、问卷创建、填写和统计。

仓库入口：[总体 README](../README.md) · [四份说明文档](../documents/README.md) · [测试与结果](../test/README.md)。以下命令均在 `code/` 目录执行。

## 1. 文件与提交要求对应关系

| 提交内容 | 文件或目录 | 说明 |
|---|---|---|
| 后端代码 | `app.py`、`survey_app/` | Flask业务应用、Waitress启动入口、认证、问卷管理、填写提交、统计和数据库访问。 |
| MongoDB schema | `数据库设计/schemas.json` | 权威结构定义，formatVersion=2，包含8个集合的严格JSON Schema校验规则与索引。后端启动时自动读取。 |
| 可选schema安装脚本 | `数据库设计/mongodb_schema.js` | 从同目录JSON读取schema，供mongosh对新库手动初始化；正常启动应用无需执行。 |
| 配置说明 | `CONFIGURATION.md`、`.env.example` | 配置项、数据库连接、端口、Cookie、会话和分页游标密钥说明。 |
| 依赖 | `requirements.txt`、`requirements-lock.txt` | 直接依赖范围、原项目已使用的精确依赖版本。 |
| MongoDB启动辅助 | `scripts/local_mongo.py` | 本地单节点副本集的启动、状态检查与停止；需要自行安装MongoDB。 |
| 操作页面 | `static/index.html`、`static/app.js`、`static/style.css` | 与后端同源的简单页面，无需Node.js或前端构建步骤，供运行后操作接口。 |
| 打包与验证记录 | `PACKAGE_MANIFEST.json`、`RUN_VERIFICATION.json` | 原独立代码包的历史SHA256清单及历史启动验证。本次组合提交包的文件校验以根目录 `SUBMISSION_MANIFEST.json` 为准。 |

后端模块职责：

| 文件 | 职责 |
|---|---|
| `survey_app/__init__.py` | 应用装配、统一错误响应和安全响应头。 |
| `survey_app/common.py` | 请求输入、BSON/JSON转换、ETag及分页参数。 |
| `survey_app/database.py` | MongoDB连接、schema和索引初始化/兼容性检查、事务重试。 |
| `survey_app/security.py` | 密码哈希、服务端会话、CSRF、请求来源检查、限流、加密分页游标。 |
| `survey_app/domain.py` | 题型和规则校验、Decimal128数字处理、填写路径计算。 |
| `survey_app/management.py` | 问卷编辑、题目排序、条件跳转、版本发布、关闭与重新开放。 |
| `survey_app/filling.py` | 填写草稿、保存、截止接纳门闩、重复策略、原子提交及幂等回执。 |
| `survey_app/statistics.py` | 先取每账号最后一次成功提交的整份答卷，再按版本/题目统计和分页。 |

## 2. 运行环境

- Python 3.11及以上；本次解压运行验证使用Python 3.14.7，具体依赖见锁定文件。
- MongoDB 7.0及以上，必须使用副本集。单节点副本集即可；普通standalone实例不支持本系统提交事务。
- Windows PowerShell启动命令如下；Python及mongod须由使用者安装，不包含在压缩包内。首次安装Python依赖需要可用的pip软件源。

查看和运行本系统不需要pytest、Allure、Node.js或原项目中的测试目录，也不需要设计文档及数据库参考业务脚本。

## 3. 从解压目录启动

在本README所在目录打开PowerShell，按顺序执行。以下命令显式调用虚拟环境Python，不依赖执行Activate.ps1。

### 3.1 建立环境、安装依赖并启动MongoDB副本集

先确认MongoDB的`mongod.exe`实际安装路径，将下方示例路径替换为实际位置，然后依次执行整段命令。已有`.env`时保留原配置；虚拟环境应在本机创建，不从其他机器直接复制。

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
& '.\.venv\Scripts\python.exe' scripts/local_mongo.py start --mongod 'C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe'
& '.\.venv\Scripts\python.exe' scripts/local_mongo.py status
```

将示例路径换成实际位置。脚本默认尝试`D:\MongoDB\bin\mongod.exe`，默认监听`127.0.0.1:27018`、副本集名`survey-rs`，数据写入解压目录下的`.local/mongodb-27018/data`。成功后状态包含`primary=True`。

如果27018已被原项目或其他MongoDB占用，脚本会因数据目录不一致而拒绝操作。请选择独立端口，例如：

```powershell
& '.\.venv\Scripts\python.exe' scripts/local_mongo.py start --port 27019 --replica-set survey-submit-rs --mongod 'C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe'
```

此时将`.env`中的`MONGODB_URI`改为`mongodb://127.0.0.1:27019/?replicaSet=survey-submit-rs`。查询或停止也应携带相同的`--port 27019 --replica-set survey-submit-rs`。本地脚本不修改Windows系统服务。

### 3.2 启动应用

```powershell
& '.\.venv\Scripts\python.exe' app.py
```

终端输出“在线问卷系统：http://127.0.0.1:5000”后，浏览器访问 <http://127.0.0.1:5000>。应用启动时自动安装schema和索引，无需额外执行SQL或mongosh脚本。

若5000被占用，在`.env`中同时修改`APP_PORT=5001`和`APP_ORIGIN=http://127.0.0.1:5001`，随后访问新地址。环境变量优先于`.env`，若配置不生效请检查当前终端是否已有同名环境变量。

新库没有预置账号，通过页面注册后登录。建议用创建者账号创建并发布问卷，再用独立浏览器会话注册填写者，打开分享链接、保存答案、确认提交，最后由创建者查看统计。

### 3.3 停止

应用终端按`Ctrl+C`。默认参数启动的数据库可以用以下命令停止，数据文件会保留：

```powershell
& '.\.venv\Scripts\python.exe' scripts/local_mongo.py stop
```

## 4. MongoDB schema说明

| 集合 | 用途 |
|---|---|
| `users` | 用户及密码哈希，规范化用户名唯一。 |
| `sessions` | 登录会话、CSRF及过期时间。 |
| `surveys` | 问卷编辑稿、收集状态、截止时间及写入门闩。 |
| `survey_versions` | 发布时的不可变问卷定义快照。 |
| `attempts` | 账号绑定的填写草稿、答案和实际路径。 |
| `responses` | 成功提交的历史答卷，schemaVersion=2。 |
| `participation_guards` | 每问卷/账号的参与标识、历史成功次数和提交序号。 |
| `submission_keys` | 提交幂等键、请求指纹和成功回执。 |

每次历史提交保留，但统计人数按账号去重，只采用最大提交序号对应的最新成功整卷。草稿和失败提交不替换旧的有效结果。数字答案和范围使用Decimal128，接口传入十进制字符串；日期存UTC BSON Date。

若需手动审阅或安装schema，保持`mongodb_schema.js`与`schemas.json`在同目录，在`数据库设计`目录运行（可选，目标库必须全新）：

```powershell
mongosh 'mongodb://127.0.0.1:27018/questionnaire_schema_demo?replicaSet=survey-rs' --file mongodb_schema.js
```

该示例只初始化名为`questionnaire_schema_demo`的库。应用实际使用的库由`MONGODB_DATABASE`决定，默认`questionnaire_app`，二者不要混淆。推荐直接让应用自动初始化。已有集合的validator不兼容或存在旧版答卷时，应用停止并提示；请使用新库或另行审查迁移，不会自动改写旧数据。

## 5. 配置说明

详细配置请查看同目录的 [CONFIGURATION.md](CONFIGURATION.md)，其中说明各配置项的默认值、用途、环境变量优先级，以及MongoDB连接、应用端口、请求来源、Cookie、会话和游标密钥的设置方法。

首次运行时，将`.env.example`复制为`.env`，结合本文第3节的启动步骤和配置文档按实际环境调整。修改端口时须同步修改`APP_ORIGIN`；MongoDB连接地址须与副本集启动参数一致。配置变更后重新启动应用。

## 6. 运行边界与已知问题

本包保留原系统实现，没有修复测试中发现的问题。既有手动测试记录了创建/提交时的离线反馈、策略/换行说明及时间字段展示问题；答案游标跨账号访问返回400而相应用例要求404。这些结果详见同级的[测试结果汇总](../test/执行记录/第一阶段历史/汇总.md)，启动验证通过不代表完整测试全部通过。

本地脚本部署为回环地址、无数据库认证的课程演示环境。需要远程部署时应另外配置受控网络、MongoDB认证/TLS和HTTPS，并按`CONFIGURATION.md`调整来源与Cookie。当前限流为单进程内存实现，未验证多实例部署和吞吐量。

## 7. 设计、实现与测试的对应入口

| 阅读目的 | 入口 |
|---|---|
| 了解系统功能和操作流程 | [系统说明](../documents/系统说明/第二阶段/系统说明.md) |
| 查看数据模型和约束 | [数据库设计](../documents/数据库设计/第二阶段/数据库设计.md)、[可执行 schema](数据库设计/schemas.json) |
| 调用 HTTP 接口 | [API说明](../documents/API说明/第二阶段/API说明.md) |
| 核对版本、规则、事务与统计的实现 | [关键逻辑说明](../documents/关键逻辑说明/第二阶段/关键逻辑说明.md) |
| 查看用例、人工截图和 API 证据 | [测试说明](../test/README.md) |

`PACKAGE_MANIFEST.json` 与 `RUN_VERIFICATION.json` 为原独立代码包的历史材料，未把历史验证改写成本次重新执行。组合包修改了 README，因此旧清单中的 README 哈希不适用于当前文件。业务 Python、页面源码、schema 与原代码目录逐字节保持一致。


## 10. 第二阶段完整系统补充

原核心结构与接口保留，新增resources、phase2_core、survey_reuse、sharing、cross_statistics、schema_tools及phase2.js；显式升级入口为scripts/phase2_schema.py。第一阶段schemas.json原样保留，空新库使用[第二阶段运行规范](数据库设计/第二阶段/运行规范/README.md)，完整旧库不自动升级。

[完整运行与升级步骤](README_PHASE2.md) · [完整系统说明](../documents/系统说明/第二阶段/系统说明.md) · [两阶段测试](../test/README.md)。原PACKAGE_MANIFEST/RUN_VERIFICATION不更新成新结果；当前全量以[根清单](../SUBMISSION_MANIFEST.json)为准。
