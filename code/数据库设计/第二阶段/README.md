# 第二阶段 MongoDB 数据设计包

[代码运行](../../README.md) · [正式文档导航](../../../documents/README.md)

本目录保留P2-1.1数据设计契约与示例；正式说明见[第二阶段系统说明](../../../documents/系统说明/第二阶段/系统说明.md)和[数据库设计](../../../documents/数据库设计/第二阶段/数据库设计.md)。原[第一阶段schema](../schemas.json)不覆盖，应用已接入独立[运行规范](运行规范/README.md)。**以下离线文件不是执行工具，也不表示实际旧业务库已经迁移。**

设计契约保留ADOPT提取出处及实例快照、跨卷关联完整性门控；实际业务由Python模块执行，运行Schema与本目录设计稿的等价规范化差异见运行规范。不要把离线检查通过等同于服务端或业务验收。

详细说明：

- [第二阶段数据库设计](../../../documents/数据库设计/第二阶段/数据库设计.md)。
- [兼容与迁移说明](../../../documents/数据库设计/第二阶段/数据库设计.md#8-旧数据的兼容与迁移)及[运行升级命令](../../README_PHASE2.md)。

## 保留、增加与修改

| 分类 | 内容 |
| --- | --- |
| 原样保留 | users、sessions、attempts、responses、participation_guards、submission_keys的schema与索引；旧ID、答案、匿名与提交序号 |
| 兼容扩展 | surveys、survey_versions的validator采用“原v1分支 OR 扩展v2分支”；v2题目可带完整reuseBinding；旧文档不批量改写 |
| 新增8业务集合 | question_resources、question_versions、question_grants、question_banks、question_bank_members、usage_summary_grants、question_usages、operation_keys |
| 新增2运维集合 | migration_runs、migration_items，保存计划、来源哈希、断点与防重信息 |

未绑定旧题继续编辑、填写、再发布及单问卷统计。主动adoption只改变当前草稿，不回写历史快照；同名旧题不自动归并。使用关系是可重建投影，答案解释依赖原发布快照。

ADOPT的`adoptionSource`必含`surveyId`、`questionId`、提取前`definitionVersion/controlRevision`、`extractedAt`、`sourceQuestionSnapshot`及规范EJSON的SHA-256。实例快照保留本地选项、必答与跳转，不含新绑定；原草稿改删后仍可核实出处。它不是发布快照，不授予问卷访问权；完整出处仅资源所有者可读，共享接口须排除私有来源。非ADOPT版本不能伪填此字段，后继沿版本链追溯。

## 文件

| 文件 | 用途 |
| --- | --- |
| [schemas.json](schemas.json) | 完整18集合目标规范；MongoDB≥7.0，副本集事务 |
| [indexes.json](indexes.json) | 原有索引与新增索引清单，无新增TTL |
| [model_examples.ejson](model_examples.ejson) | 同时包含旧格式和新绑定的BSON扩展JSON示例，不自动导入 |
| [change_manifest.json](change_manifest.json) | 保留、增加、修改清单及原schema SHA-256 |
| [transaction_boundaries.json](transaction_boundaries.json) | 10类用例事务边界、CAS与重试约定 |
| [usage_summary_policy.json](usage_summary_policy.json) | SUMMARY_V1授权方向、白名单与禁止字段 |
| [migration_plan.json](migration_plan.json) | 分阶段切换、失败恢复与幂等方案，executionProvided=false |
| [schema_plan.js](schema_plan.js) | 只输出DDL计划数据，无数据库连接和apply函数 |
| [query_contracts.js](query_contracts.js) | 最新整卷、完整性信封、整份报告门控、摘要授权投影；不是完整统计服务 |
| [transaction_contracts.js](transaction_contracts.js) | 只读取并打印事务约定 |
| [migration_plan.js](migration_plan.js) | 只打印迁移方案，拒绝apply等执行参数 |
| [offline_review.js](offline_review.js) | Node内置库完成文件一致性检查，无MongoDB访问 |
| [query_regression.js](query_regression.js) | 以Mingo在内存模拟聚合的回归用例，无MongoDB访问；开发校验另需mingo/bson |
| [offline_review_report.json](offline_review_report.json) | 本次离线检查结果与证据边界，包含PDF页数、示例和保留文件核对；不是迁移执行报告 |

## 离线查看

以下命令仅使用Node.js和本地文件，**不要用mongosh直接加载本目录，也不要把目标schemas.json覆盖上一级原文件**。

```powershell
# 在questionnaire仓库根目录执行；不依赖原开发机的绝对路径。
Set-Location -LiteralPath './code/数据库设计/第二阶段'
node .\offline_review.js
node .\schema_plan.js --plan
node .\migration_plan.js --plan
node .\transaction_contracts.js
```

`offlineChecks: PASS`只证明所检查的文件契约相符及报告门控用例通过，不证明真实MongoDB已接受DDL、现有旧数据符合validator或业务行为正确。可选的`query_regression.js`接收包含mingo与bson的本地node_modules绝对路径；模拟引擎不能代替MongoDB服务端验证。

`crossSurveyCandidates`仅构造pipeline：每份最新整卷输出一个`CHECKED_RESPONSE`或`INTEGRITY_ERROR`信封；lookup数组基数、问卷所有者、guard所属卷/账号和数据形状在目标题目、路径、非空筛选前校验。正常未命中为`candidates=[]`；缺失/重复关联不能静默跳过。

调用端须在同一snapshot预检**全部**请求问卷的所有权（包括无答卷问卷），完整消费信封，再经`buildCrossSurveyReport`生成报告。任一完整性错误返回HTTP500 `DATA_INTEGRITY_ERROR`，游标中断同样不得发布部分结果；不得丢掉错误信封、回退旧答或换当前快照。诊断仅入受限日志，页面只见错误码与requestId。回调只做请求内计算，不推送/缓存部分指标。检测范围是本次选中的最新整卷，不是全历史审计。

Python服务承担可信会话、ObjectId转换、可比性、实例选择、空答案校验、Decimal指标、游标签名和输出白名单；内部信封禁止直接返回页面。当前非法ID500等已知差异见[API说明第9节](../../../documents/API说明/第二阶段/API说明.md#9-当前实现与设计文档的差异)，不把本设计包当作无缺陷实现证明。

## 迁移审查边界

默认不批量生成题库资源、不改旧发布快照、不自动追授摘要权限。旧responses若不是现基线v2，本计划先阻断，另审第一阶段序号迁移。当前ensure_schema精确比较validator，因此目标DDL和兼容应用必须协调切换，不能让不认识绑定的旧写入端混跑。

1.1修订的是未应用的目标方案；如外部曾试用1.0，缺失ADOPT证据须另审修复，禁止根据当前草稿倒推原实例。迁移预检发现快照/guard关联异常应先阻断，不得以统计滤除掩盖缺陷。

先停写备份、恢复演练、兼容端就绪、分步DDL、验收，再由用户审查是否激活。激活后回退需保全新增数据并由双格式兼容端接管，不能靠删新集合或覆盖恢复丢弃新答卷。本目录JS只提供方案与离线契约，不执行迁移；实际升级工具及命令见[运行升级说明](../../README_PHASE2.md)，本轮未迁移实际旧业务库。
