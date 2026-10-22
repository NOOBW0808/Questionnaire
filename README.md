# MongoDB 在线问卷系统

本仓库统一提交课程项目的**代码、测试和四份说明文档**。支持注册登录、问卷创建与发布、四种题型、条件跳转、答案校验、保存提交及结果统计。采用 Flask/PyMongo 与 MongoDB 副本集，无需 Node.js 或前端构建步骤。

## 1. 仓库导航

```text
questionnaire-github/
├─ README.md                        总体说明与操作入口
├─ .gitignore                       排除虚拟环境、密钥和本地运行文件
├─ .gitattributes
├─ SUBMISSION_MANIFEST.json          本次整理的文件校验及来源记录
├─ code/                 可运行前后端、schema、依赖与配置
│  ├─ README.md
│  ├─ app.py
│  ├─ survey_app/
│  ├─ static/
│  └─ 数据库设计/
├─ test/                 用例、API源码、人工记录与Allure证据
│  ├─ README.md
│  ├─ run_api_tests.py
│  ├─ 测试步骤/
│  └─ 执行记录/
└─ documents/
   ├─ README.md                     文档导航
   ├─ 系统说明.md
   ├─ 数据库设计.md
   ├─ API说明.md
   └─ 关键逻辑说明.md
```

| 需要做什么 | 阅读入口 |
|---|---|
| 了解系统与操作流程 | [系统说明](documents/系统说明.md) |
| 运行和配置应用 | [代码 README](code/README.md)、[配置说明](code/CONFIGURATION.md) |
| 理解集合与约束 | [数据库设计](documents/数据库设计.md)、[可执行 schema](code/数据库设计/schemas.json) |
| 调用接口 | [API说明](documents/API说明.md) |
| 审查关键实现 | [关键逻辑说明](documents/关键逻辑说明.md) |
| 查看用例、结果与复跑方法 | [测试 README](test/README.md)、[结果汇总](test/执行记录/汇总.md) |

建议按“系统说明 → 数据库设计 → API说明 → 关键逻辑 → 代码运行 → 用例与证据”阅读。文档直接链接代码，测试直接加载同级代码与 schema，三部分可整体移动、下载或作为 GitHub 仓库根目录。

## 2. 启动应用

需要 Python 3.11+、MongoDB 7.0+；MongoDB 必须启用副本集以支持事务。在本 README 所在目录打开 PowerShell：

```powershell
Set-Location './code'
python -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r requirements-lock.txt
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
# 替换为本机实际 mongod.exe 路径。
& './.venv/Scripts/python.exe' scripts/local_mongo.py start --mongod 'C:/Program Files/MongoDB/Server/8.0/bin/mongod.exe'
& './.venv/Scripts/python.exe' app.py
```

浏览器访问 <http://127.0.0.1:5000>，自行注册创建者和填写者账号。默认数据库为 `questionnaire_app`，应用启动时读取本目录 schema 并安装索引。若端口或数据库被其他项目占用，按[代码启动说明](code/README.md#3-从解压目录启动)更换 MongoDB 端口、连接地址及应用端口。配置文件和虚拟环境在本机生成，未随仓库复制。

## 3. 查看测试报告

另开一个位于仓库根目录的 PowerShell：

```powershell
python -m http.server 8088 --bind 127.0.0.1 --directory './test/执行记录/Allure/报告'
```

访问 <http://127.0.0.1:8088>。该步骤不需要 MongoDB 或 Allure CLI。API 复跑命令见[测试 README](test/README.md#3-在本组合仓库复跑-api-测试)，入口加载本仓库的 `code`，不依赖原项目。

已提交的历史批次共 143 条执行记录：137 通过、4 失败、2 跳过。原有 142 项规格外补充了 1 项 API 测试；当前实现保留已知问题。本次整理没有将历史测试重新判定，复跑输出进入独立新目录。

## 4. 设计与验证的边界

每个账号只管理自己创建的问卷；填写也必须登录。匿名表示向创建者隐藏身份，非免登录。发布后可继续修改编辑稿，已有填写绑定原发布版本。允许重复填写时保留全部历史答卷，但统计仅取每账号最新成功整卷，最新跳题或空值不从旧答卷补回。

数据库设计文档包含设计阶段参考脚本说明；当前可执行实现以 `code/survey_app/` 和 `code/数据库设计/schemas.json` 为准，参考包清单不代表所有历史脚本均随仓库提交。API说明列出的实现差异和测试失败均原样保留。

## 5. 提交范围与文件校验

以本文件夹作为 GitHub 仓库根目录，提交这里的三个子目录及根目录文件。原工作区中的虚拟环境、`.env`、数据库文件、运行密钥、缓存未复制；`.env.example`、已提交 Allure 报告、原始测试证据均保留。单个文件应符合 GitHub 大小限制。

整理只修改路径和说明，新增独立 API 复跑入口；业务源码、schema 和历史执行证据保持原字节。`SUBMISSION_MANIFEST.json` 记录当前文件 SHA256、来源校验和修改列表；仓库通过 `.gitattributes` 关闭自动换行转换，便于下载后逐字节核对。代码目录内的历史包清单及启动记录只说明原打包批次。
