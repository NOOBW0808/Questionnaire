# 在线问卷系统 API 说明

[文档目录](../../README.md) · [第一阶段](../第一阶段/API说明.md) · [仓库首页](../../../README.md) · [运行代码](../../../code/README.md) · [测试材料](../../../test/README.md)

本文沿用第一阶段“公共约定→数据结构→账号→管理→题目→填写→统计→错误→差异”的章节顺序。原23个业务接口保留，下面重点说明新增接口及原接口变化；路径均省略 `/api/v1`。实现依据为当前 `code/survey_app/` 与[运行Schema](../../../code/数据库设计/第二阶段/运行规范/schemas.json)。

## 1. 公共约定

### 1.1 请求与响应

JSON信封、requestId、UTC时间、十进制字符串及8 MiB请求上限沿用。新增 `GET /capabilities` 返回 `{schemaStage, phase2Available}`，需要登录；完整旧库返回不可用能力，新业务返回409 `SCHEMA_UPGRADE_REQUIRED`，原业务可继续运行。

新增创建型POST首次成功通常为201，成功重放为200，返回最小ID、`resultEtag`及 `replayed`；具体接口见下表。DELETE题库成功204，无响应体。未知结构或未激活升级在启动阶段阻断，不自动改写旧库。

### 1.2 登录、权限与安全头

原Cookie、CSRF及Origin检查不变。资源所有者可管理资源；接收者只读取被授权版本并选用/派生；题库和问卷写入只限各自所有者。题目共享不开放账号列表、他人问卷管理或答案。

跨用户导入须确认向资源所有者开放SUMMARY_V1使用摘要，不能从“可使用题目”推定“可看他人答卷”。跨卷统计只允许当前账号创建的问卷。

### 1.3 并发控制与幂等

| 操作 | 额外请求头 |
|---|---|
| 原问卷编辑、发布及新增导入/换版/ADOPT/派生改绑 | 原问卷 `If-Match: "definition-N-control-N"`。 |
| 修改工作稿、固定后继、恢复 | `If-Match: "resource-N"`。 |
| 题库改名、删除、成员更新/移除 | `If-Match: "bank-N"`。 |
| 首次版本共享或首次单独建立摘要授权 | `If-None-Match: *`。 |
| 更新已有版本共享/摘要授权 | 各自 `If-Match: "grant-N"` / `"summary-N"`。 |
| 创建资源/题库、固定、恢复、派生、导入、ADOPT、派生改绑POST | 另附16～128位无空白可打印ASCII `Idempotency-Key`。 |

原attempt保存及答卷提交头不变。不同对象的ETag不可互换；缺少条件头428，错误或陈旧409。同账号同用例同键同指纹回放原成功结果，异指纹409；未知结果保留原body、ETag和key。显式换版PUT不是创建型幂等POST，未知结果重新GET确认，不能承诺相同凭据重放。

### 1.4 分页

资源、版本、谱系、题库成员、授权、摘要及使用列表使用 `limit/cursor/nextCursor`。游标绑定账号和查询范围，不能跨资源/筛选条件使用；范围过滤及授权先于使用摘要分页、计数。

跨卷统计返回一次完整报告，不提供跨请求快照游标。当前最多50个目标版本、100份问卷；同一发布快照最多选择一个统计实例。

## 2. 主要数据结构

### 2.1 问卷视图 SurveyView

原字段不变。管理视图中已绑定题目增加 `reuseBinding`；当前草稿与已发布快照仍分别解释，不把资源最新版当作当前发布内容。

| 新增结构 | 主要字段与含义 |
|---|---|
| 资源视图 | `resourceId, ownership, createdAt, updatedAt`；本人另有 `latestVersionId, versionCounter, workingDraft?`，共享接收者看不到私有工作稿及未授权版本指针。 |
| 固定版本 | `questionVersionId, resourceId, versionNo, parentVersionId, originKind, content, contentHash, createdAt`；按来源包含 `derivedFrom/restoredFromVersionId/adoptionSource`。 |
| 问卷绑定 | `resourceId, questionVersionId, contentHash, optionMap, semanticsSnapshot`；选项映射为 `{semanticOptionId, localOptionId}`。 |
| 题库视图 | `bankId, name, state, createdAt, updatedAt`；详情另含分页 `members`，各成员指定资源及固定版本。 |

ADOPT完整出处仅资源所有者可见，共享视图剔除私有源问卷、实例快照等证据。实例身份 `questionId` 与独立资源/版本不同，换版保留qid不表示没有新版本。

### 2.2 题目与答案

原四题型、答案空值及校验规则不变。固定内容为 `{type,title,validation,options?,semantics?}`；选择项为 `{semanticOptionId?,label}`，新增语义ID由服务端生成，编辑保留原语义选项时提交其ID。`semantics`包含可选 `measure/unit/timeBasis/population/notes`。

必答、排序和跳转不放入固定内容。新导入实例默认 `required=false, jumpRules=[]`；换版需要时显式提交完整optionMap，保留本地规则须仍合法。普通题目PATCH不能直接覆盖绑定内容。原未绑定旧题仍按原接口编辑。

### 2.3 填写视图 AttemptView

字段及保存/提交协议不变，`definition`来自attempt绑定的原发布快照。输出只含执行所需题干、选项、校验及跳转，不含reuseBinding、资源出处和授权；不能因当前题库更新改用新定义。

## 3. 账号接口（4 个，沿用）

原注册、登录、当前账号和退出接口无变化。共享使用完整 `username`定位已存在接收者，不新增全局账号搜索接口；不存在或本人账号返回422 `RECIPIENT_INVALID`。

新增 `GET /capabilities` 为登录后的能力查询，不替换原账号接口，也不计入原4个账号接口。

## 4. 问卷管理接口（原6个，新增题库与摘要管理）

原6个接口保留。`GET /surveys/{s}`返回管理绑定；发布额外校验版本、内容/哈希及映射，复制绑定到整卷快照并保存使用行，失败不改变原发布。

| 方法与路径 | 输入与前置条件 | 成功结果 |
|---|---|---|
| `POST /question-banks` | `{name}`，幂等键；名称1～200字符。 | 首次201/重放200：`{bankId,resultEtag,replayed}`及题库ETag。 |
| `GET /question-banks` | `limit,cursor`。 | 本人有效题库 `items,nextCursor`。 |
| `GET /question-banks/{b}` | 本人题库ID，分页参数。 | 题库详情、`members,nextCursor`及ETag。 |
| `PATCH /question-banks/{b}` | `{name}`，题库If-Match。 | 题库视图及新ETag。 |
| `DELETE /question-banks/{b}` | 题库If-Match。 | 204软删除；资源、版本、问卷及答卷保留。 |
| `PUT /question-banks/{b}/members/{r}` | `{questionVersionId}`，题库If-Match；须有版本使用权。 | 题库视图及新ETag，成员选版不自动传播。 |
| `DELETE /question-banks/{b}/members/{r}` | 题库If-Match。 | 移出成员，返回题库视图及新ETag。 |
| `GET /surveys/{s}/usage-summary-grants` | 本人问卷，分页。 | 已有摘要授权列表，包括不再位于草稿的资源。 |
| `GET /surveys/{s}/usage-summary-grants/{r}` | 本人问卷及可访问资源。 | 摘要授权与summary ETag，不存在404。 |
| `PUT /surveys/{s}/usage-summary-grants/{r}` | `{enabled,policyVersion:"SUMMARY_V1"}`；首次If-None-Match，更新summary If-Match。 | 授权视图及新ETag，同时推进问卷控制修订；应重读问卷后继续编辑。 |

摘要授权由问卷创建者授给资源所有者，关闭只停止他人摘要展示，不删除已使用实例或旧快照；导入确认流程见下一节。

## 5. 题目和跳转接口（原5个，新增资源与复用接口）

原本地题目/跳转接口继续使用。绑定题仅本地配置可直接改，内容变化应生成版本或派生后显式改绑。

| 方法与路径 | 请求与前置条件 | 成功结果 |
|---|---|---|
| `POST /question-resources` | `{content,fixed?}`，fixed默认true，幂等键。 | 201/200：资源ID、固定时的版本ID、`resultEtag,replayed`。 |
| `GET /question-resources` | `scope=OWN/SHARED`，默认OWN，分页。 | 本人或共享资源 `items,nextCursor`。 |
| `GET /question-resources/{r}` | 有权资源。 | 资源视图；所有者取得资源ETag。 |
| `PATCH /question-resources/{r}/draft` | `{content}`，本人资源If-Match。 | 私有工作稿及新资源ETag；不覆盖固定版本。 |
| `POST /question-resources/{r}/versions` | `{baseVersionId?,content?}`，资源If-Match及幂等键；已有版本须明确base，无content时使用工作稿。 | 新版本ID、资源ID、`resultEtag,replayed`，201/200。 |
| `GET /question-resources/{r}/versions` | 分页；接收者只见获授权版本。 | 版本 `items,nextCursor`。 |
| `GET /question-resources/{r}/versions/{v}` | 资源与版本所属匹配且有权。 | 固定版本详情。 |
| `GET /question-resources/{r}/lineage` | 分页。 | 带父/派生/恢复关系的可见版本页，不遍历私有上游。 |
| `POST /question-resources/{r}/restorations` | `{baseVersionId,restoreFromVersionId}`，资源If-Match、幂等键。 | 新RESTORE版本，201/200，不覆盖历史。 |
| `POST /question-resources/{r}/versions/{v}/forks` | `{content?}`，幂等键。 | 本人新资源/初版、来源及ETag，201/200。 |
| `POST /surveys/{s}/question-imports` | `{resourceId,questionVersionId,usageSummaryConsent?}`，问卷If-Match及幂等键。 | 本卷新实例、资源/版本ID和问卷ETag，201/200。 |
| `PUT /surveys/{s}/questions/{q}/binding` | 同上可带 `optionMap`，问卷If-Match。 | 保留本卷qid的明确换版结果及新ETag，200。 |
| `POST /surveys/{s}/questions/{q}/adoption` | `{semantics?}`，本人未绑定题，问卷If-Match及幂等键。 | 新资源/初版、当前绑定及ETag，201/200；捕获改绑前出处。 |
| `POST /surveys/{s}/questions/{q}/derivations` | `{content}`，已绑定题，问卷If-Match及幂等键。 | 本人新派生资源/初版与原子改绑结果，201/200。 |
| `GET /question-resources/{r}/versions/{v}/grants` | 仅原作者，分页。 | 授权 `items,nextCursor`，条目含接收者及各grant的etag。 |
| `PUT /question-resources/{r}/versions/{v}/grants` | `{username}`；首次If-None-Match:*，更新用该grant If-Match。 | 200指定版本USE/FORK授权及grant ETag。 |
| `GET /question-resources/{r}/usages` | 可选 `questionVersionId,usageStage,surveyStatus,limit,cursor`。 | `items,coverage,totalVisible,nextCursor`，先权限复核再分页。 |

跨用户导入/换版首次或重新开启摘要时，确认字段为：

```json
{
  "usageSummaryConsent": {"accepted": true, "policyVersion": "SUMMARY_V1"}
}
```

已有同范围有效授权可复用；未确认返回409 `USAGE_SUMMARY_CONSENT_REQUIRED`，不创建实例。授权、绑定及使用行原子保存。关闭与导入竞争同一问卷控制版本，旧页面不能隐式重新开启。

使用阶段为 `DRAFT/CURRENT_PUBLISHED/HISTORICAL_PUBLISHED`；问卷状态筛选支持 `DRAFT/PUBLISHED/CLOSED/EXPIRED`。SUMMARY_V1条目严格为：`surveyId,surveyTitle,ownership,resourceId,questionVersionId,publishedVersion,usageStage,surveyStatus,instanceCount`；不含填写链接、账号或答案。

## 6. 填写和提交接口（5 个，沿用）

原开始、恢复、保存、提交及记录访问协议保留。唯一必要调整是对外定义只显示执行字段；读取、保存及提交始终使用原发布版本，当前收集状态/截止继续生效。源版本、题库更新或删除不改原attempt，未绑定旧题仍能填写及单卷统计。

## 7. 统计接口（原3个，新增跨卷接口）

原单卷统计与答案分页不扩大权限。新增 `POST /question-statistics/cross-survey`，200返回完整聚合报告，不写入新的统计缓存或答案。

```json
{
  "targets": [{"resourceId": "实际24位资源ID", "questionVersionId": "实际24位版本ID"}],
  "surveyIds": ["实际24位本人问卷ID"],
  "mode": "GROUPED",
  "instanceSelectors": [{"surveyId": "实际24位本人问卷ID", "publishedVersion": 1, "questionId": "实际本卷题目ID"}]
}
```

示例ID须替换为真实响应，`mode`默认GROUPED。重复目标或范围拒绝，同一快照多个目标实例必须显式选一个。

### 7.1 统计视图

GROUPED按固定版本与执行上下文分组。MERGED必须提供 `comparisonSpec` 的 `contextConfirmed:true,rationale`，服务端核对题型、测量、单位、时间、人群；缺失说明可用逐版本 `semanticDeclarations`补充，不能覆盖已有冲突。

选择题合并需完整显式映射，每个目标版本均提供一项：

```json
{
  "contextConfirmed": true,
  "rationale": "已逐项核对题意和前置路径",
  "categories": [{"categoryId": "yes", "label": "是"}, {"categoryId": "no", "label": "否"}],
  "optionMappings": [{"questionVersionId": "实际版本ID", "mapping": [
    {"semanticOptionId": "实际是选项ID", "categoryId": "yes"},
    {"semanticOptionId": "实际否选项ID", "categoryId": "no"}
  ]}]
}
```

数值分箱放 `comparisonSpec.bins`，为2～101个严格递增十进制字符串。区间含下溢、左闭右开及上溢；未分箱返回频次，不同值超过1000项要求提供bins。同源/同哈希不自动合并，不支持单位转换。

| 响应字段 | 含义 |
|---|---|
| `targets,surveyIds,mode,comparisonSpec,instanceSelectors` | 实际统计范围和比较解释。 |
| `latestWholeResponseCount` | 每卷每账号最新成功整卷数，先选整卷再过滤目标。 |
| `groups[].groupKey/type/questionVersionIds` | 分组身份、题型及版本。 |
| `answeredParticipationCount/distinctAnsweredAccountCount/reachedCount` | 非空贡献、独立作答账号及路径经过次数；同账号跨A/B各贡献一次。 |
| `average,distribution` | 数字均值字符串及频次/分箱；零数值样本均值null，不公开sum。 |
| `options` | 选择题语义选项或合并类别计数，多选合计可超过答卷数。 |
| `exclusions,contextBasis,generatedAt` | 正常未命中/跳题/空答的数量、上下文依据及生成时间。 |

快照、参与记录、账号、路径或答案异常使整份失败；完整消费游标后才返回结果，不回退旧答、不换用当前快照、不返回部分指标。`distinctAnsweredAccountCount`按每组实际贡献账号去重，不公开账号明细。

### 7.2 填空答案列表

原单卷文本/数字答案分页保持原权限及匿名规则。跨卷不新增原始答案列表：文本只返回有效作答数，数字仅均值与分布。内部账号、参与标识与原文均不输出。

## 8. 常见失败响应

| HTTP / 业务码 | 新增条件与处理 |
|---|---|
| 401 / 403 | 仍按原登录、CSRF及Origin规则。 |
| 404 `NOT_FOUND` | 有效格式对象不存在、无权访问或版本不属目标资源。 |
| 428 `PRECONDITION_REQUIRED` | 缺If-Match或首次授权缺If-None-Match。 |
| 409 `VERSION_CONFLICT/IDEMPOTENCY_CONFLICT` | 对象陈旧或同键请求不一致。 |
| 409 `SCHEMA_UPGRADE_REQUIRED` | 完整旧结构尚未显式升级。 |
| 409 `USAGE_SUMMARY_CONSENT_REQUIRED` | 跨用户导入未明确同意摘要策略。 |
| 422 `BINDING_INVALID` | 固定内容/哈希或完整映射、规则不一致。 |
| 422 `COMPARISON_REQUIRED/NOT_COMPARABLE/MAPPING_INVALID` | 缺比较确认、语义/单位冲突或选项映射不全。 |
| 422 `INSTANCE_SELECTION_REQUIRED/INSTANCE_INVALID` | 同快照多实例未选择，或所选实例无效。 |
| 422 `BINS_INVALID/DISTRIBUTION_TOO_LARGE` | 分箱不合法或不同数值过多。 |
| 500 `DATA_INTEGRITY_ERROR` | 本次最新整卷关联或内容损坏；没有部分data。 |
| 503 `COMMIT_UNCERTAIN`或数据库错误 | 保留原请求核实；统计整份不出结果。 |

## 9. 当前实现与设计文档的差异

| 项目 | 当前实现与限制 |
|---|---|
| 非法ID转换 | `oid()`未捕获BSON `InvalidId`，非法资源ID及统计范围ID预期404、实际500；两项新增失败保留，尚未修复。 |
| 原答案游标 | `TC07-A25`预期跨资源404，实际400 `CURSOR_INVALID`，原失败保留。 |
| 精确截止等号 | 无受控MongoDB服务端时钟，`TC08-A05`明确跳过，截止前后测试不能替代。 |
| 谱系 | 返回经授权的版本关系页，不提供跨私有上游的无限递归树。 |
| 摘要与统计规模 | 摘要当前在事务内复核并由应用分组，未验证大规模性能；跨卷受50版本/100问卷及分布容量限制。 |
| 显式换版 | PUT/CAS事务支持失败回滚，未提供创建型POST的operation_keys重放协议。 |
| 迁移 | 工具与隔离演练已提供，不代表实际旧业务库已迁移；无法证明来源或结构漂移时阻断，不删除历史。 |

本轮完整范围213项，209通过、3失败、1跳过，详见[测试与实际证据](../../../test/README.md)。原第一阶段规格和失败记录不因新增文档改判。
