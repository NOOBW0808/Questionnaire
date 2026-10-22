# 在线问卷系统 API 说明

[文档目录](README.md) · [仓库首页](../README.md) · [运行代码](../code/README.md) · [测试材料](../test/README.md)

本文依据数据库设计、[可执行 schema](../code/数据库设计/schemas.json) 与当前 Flask 路由实现整理，记录实际提供的 23 个业务接口。以下路径均省略统一前缀 `/api/v1`；默认服务地址为 `http://127.0.0.1:5000`。

## 1. 公共约定

### 1.1 请求与响应

除 DELETE 题目接口无需请求体外，写接口使用 `Content-Type: application/json`，无业务参数的写接口也须发送 `{}`。请求体必须为对象；缺少必要字段或包含未知字段会被拒绝。问卷、账号、填写和答卷 ID 使用 24 位十六进制字符串；题目、选项、规则和分享标识由服务端生成。

成功响应统一为：

```json
{
  "data": {"surveyId": "507f1f77bcf86cd799439011"},
  "requestId": "req_example"
}
```

错误响应统一为：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "问卷已被修改，请重新加载后操作"
  },
  "requestId": "req_example"
}
```

`error.details` 在需要定位题目或说明字段错误时出现。HTTP 204 无响应体。响应头提供 `X-Request-ID`，API 响应设置 `Cache-Control: no-store`。

时间输出为 UTC ISO 8601 字符串，例如 `2026-10-21T10:00:00.000Z`；截止时间输入必须带时区，`null` 表示不设截止。数字答案、数字边界和平均值使用十进制字符串；人数、版本及序号使用 JSON 整数。请求体上限为 8 MiB。

### 1.2 登录、权限与安全头

登录成功后，浏览器保存 `survey_session` Cookie，并从响应取得 `csrfToken`。该 Cookie 为 HttpOnly、SameSite=Lax；HTTPS 环境通过配置启用 Secure。

除注册和登录外，所有业务接口都要求登录，包括 `/public-surveys/...`。已登录的 POST、PUT、PATCH、DELETE 请求必须附带 `X-CSRF-Token`；服务端还检查 Origin、Referer 或浏览器跨站标记，来源须符合 `APP_ORIGIN`。注册和登录也执行来源检查。

问卷管理及统计接口限创建者调用；填写记录只允许对应填写者访问。客户端不提交 `ownerId`、`userId` 或统计身份字段。

### 1.3 并发控制与幂等

| 操作 | 必须携带的额外请求头 |
|---|---|
| 修改问卷、发布、关闭、增删改题目、排序、配置规则 | `If-Match: "definition-<版本>-control-<修订号>"` |
| 保存或提交填写 | `If-Match: "attempt-<修订号>"` |
| 提交填写 | 另附 `Idempotency-Key`，16～128 位无空白可打印 ASCII 字符。 |

ETag 来自最近一次详情或修改响应的 `ETag` 响应头，必须保留双引号；问卷列表不提供可直接用于修改的 ETag。缺少 `If-Match` 返回 428，格式不对或版本陈旧返回 409。保存后应使用新 ETag 提交。

提交超时或结果不确定时，以**原尝试 ID、原提交 If-Match 和原幂等键**重试。同一请求成功重放返回 200，首次接纳返回 201；同键用于不同尝试或不同 If-Match 返回 409。

### 1.4 分页

列表使用返回的 `nextCursor` 继续查询，不能自行构造游标。`limit` 默认 20，可取 1～100。游标加密且绑定账号与查询范围，有效期 24 小时；篡改、过期或跨范围复用返回 400 `CURSOR_INVALID`。

统计版本分页使用 `versionCursor` 与 `nextVersionCursor`，每页最多 20 个版本。`publishedVersion` 为可选正整数，用于指定单一版本，不能同时携带有效版本分页游标。跨页访问不保证固定在同一历史快照。

## 2. 主要数据结构

### 2.1 问卷视图 SurveyView

| 字段 | 含义 |
|---|---|
| `surveyId`、`publicCode`、`shareUrl` | 问卷 ID、分享标识、相对页面链接 `/survey/{publicCode}`。 |
| `state` | 数据库状态：`DRAFT`、`PUBLISHED`、`CLOSED`。 |
| `effectiveState` | 展示状态；已发布且到期时为 `EXPIRED`，不额外修改数据库状态。 |
| `definitionVersion`、`controlRevision` | 定义编辑版本与管理修订号，共同组成管理 ETag。 |
| `publishedVersion` | 当前发布版本号，未发布为 `null`。 |
| `deadlineAt`、`createdAt`、`updatedAt` | 截止、创建、上次管理修改时间。 |
| `draft` | 当前编辑定义：`title`、`description`、`policy`、`questions`。 |

`draft.policy` 为 `{anonymous, allowRepeat}`。`draft` 可能已修改但尚未再次发布，不能视为当前对填写者生效的定义。列表项不含 `draft`，另有草稿 `title`。

### 2.2 题目与答案

题目公共字段为 `questionId`、`type`、`title`、`required`、`validation`、`jumpRules`；选择题另有 `options: [{optionId, label}]`。

| `type` | `validation` 可用字段 | 答案 `value` | 显式空答案 |
|---|---|---|---|
| `SINGLE` | 空对象 `{}` | 一个有效 `optionId` 字符串 | `null` |
| `MULTIPLE` | `minSelections`、`maxSelections`、`exactSelections` | 不重复的有效 `optionId` 数组 | `[]` |
| `TEXT` | `minLength`、`maxLength` | 文本字符串，保存时去除首尾空白 | `""` |
| `NUMBER` | `minValue`、`maxValue`、`integerOnly` | 可精确表示为 Decimal128 的有限十进制字符串 | `null` |

数量和长度配置为非负整数；数字边界使用字符串，`integerOnly` 默认 `false`。非空答案按配置校验，空答案另外受 `required` 约束。文本答案最多 10000 字符。

### 2.3 填写视图 AttemptView

`attemptId`、`surveyId`、`publishedVersion`、`anonymous`、`revision`、`status`、`answers`、`visitedQuestionIds`、`nextQuestionId`、`responseId`、`submittedAt`、`updatedAt`。

`status` 为 `IN_PROGRESS` 或 `SUBMITTED`；未提交时 `responseId`、`submittedAt` 为 `null`。`answers` 中每项为 `{questionId, type, value}`。开始和读取填写接口另返回绑定的 `definition`；保存接口另返回 `removedQuestionIds`。后端计算路径，客户端不能指定访问路径或下一题。

## 3. 账号接口（4 个）

| 方法与路径 | 请求 | 成功结果 |
|---|---|---|
| `POST /auth/register` | `{username, password}` | 201：`{userId, username, createdAt}`；注册后仍需登录。 |
| `POST /auth/login` | `{username, password}` | 200：`{user: {userId, username, createdAt}, csrfToken}`，并设置会话 Cookie。 |
| `GET /auth/me` | 无请求体 | 200：当前 `user` 与 `csrfToken`。 |
| `POST /auth/logout` | `{}`，附 CSRF | 204：删除当前会话与 Cookie。 |

用户名去除首尾空白后为 1～64 字符，按 NFKC 规范化和大小写折叠检查唯一性；密码为 8～128 字符，保留大小写与空白，没有额外的大小写或特殊字符组合要求。非法 Unicode 代理字符被拒绝。

用户名冲突返回 409 `USERNAME_EXISTS`；合法格式但账号或密码不正确返回 401 `LOGIN_FAILED`；格式错误返回 422。注册、登录分别按来源 IP 限制每分钟 10、20 次。

## 4. 问卷管理接口（6 个）

| 方法与路径 | 输入 | 成功结果 |
|---|---|---|
| `POST /surveys` | 必填 `title`，可选字段见下表。 | 201：SurveyView 与 ETag。 |
| `GET /surveys` | 查询参数 `limit`、`cursor`、`status`，均可选。 | 200：`{items, nextCursor}`，只列出本人问卷。 |
| `GET /surveys/{surveyId}` | 路径 ID。 | 200：SurveyView 与 ETag。 |
| `PATCH /surveys/{surveyId}` | 创建字段的任意子集，附管理 If-Match。 | 200：SurveyView 与新 ETag。 |
| `POST /surveys/{surveyId}/publish` | `{}`，附管理 If-Match。 | 200：SurveyView 与新 ETag。 |
| `POST /surveys/{surveyId}/close` | `{}`，附管理 If-Match。 | 200：SurveyView 与新 ETag。 |

| 创建或修改字段 | 约束 | 创建默认值 |
|---|---|---|
| `title` | 去首尾空白后 1～200 字符 | 必填 |
| `description` | 去首尾空白后 0～4000 字符 | `""` |
| `anonymous` | 布尔值，对创建者匿名 | `false` |
| `allowRepeat` | 布尔值，是否允许重新填写 | `false` |
| `deadlineAt` | 带时区的时间或 `null` | `null` |

`status` 可取 `DRAFT`、`PUBLISHED`、`CLOSED`、`EXPIRED`；`PUBLISHED` 筛选正在收集的已发布问卷。列表按 `updatedAt`、ID 倒序排列。

发布要求 1～200 道题，选择题至少 1 个选项，校验与跳转配置一致，截止时间为空或晚于数据库当前时间。已过期时发布返回 409 `DEADLINE_PASSED`；关闭草稿返回 409 `NOT_PUBLISHED`。关闭后可以再次发布，定义未改动时复用原发布版本。

## 5. 题目和跳转接口（5 个）

以下接口均要求问卷所有权、CSRF 和最新管理 If-Match。

| 方法与路径 | 输入 | 成功结果 |
|---|---|---|
| `POST /surveys/{surveyId}/questions` | 必填 `type`、`title`；可选 `required`、`options`、`validation`。 | 201：**整份 SurveyView** 与新 ETag。 |
| `PATCH /surveys/{surveyId}/questions/{questionId}` | 上述字段的子集。 | 200：整份 SurveyView 与新 ETag。 |
| `DELETE /surveys/{surveyId}/questions/{questionId}` | 无请求体。 | 204，响应头含新 ETag。 |
| `PUT /surveys/{surveyId}/question-order` | `{questionIds: [...]}`，必须包含全部题目且不重复。 | 200：整份 SurveyView 与新 ETag。 |
| `PUT /surveys/{surveyId}/questions/{questionId}/jump-rules` | `{rules: [...]}`，空数组用于清除。 | 200：整份 SurveyView 与新 ETag。 |

题干为 1～500 字符，`required` 默认 `false`，每题最多 100 个选项、100 条规则。新选项提交 `{label}`，标签 1～200 字符；修改已有选项时保留原 `optionId`。选项列表和 `validation` 若出现，均替换对应部分，不做内部增量合并。题型不能直接转换。

新增数字题示例：

```json
{
  "type": "NUMBER",
  "title": "请输入年龄",
  "required": true,
  "validation": {"minValue": "0", "maxValue": "120", "integerOnly": true}
}
```

每条规则必填 `priority`、`operator`、`value`、`target`；修改已有规则可附原 `ruleId`，新规则 ID 由服务端生成。`priority` 为不重复的非负整数，越小越先匹配；`target` 为后续题目 ID 或 `END`。

| 题型 | 条件操作符 | `value` |
|---|---|---|
| `SINGLE` | `EQ` | 一个选项 ID。 |
| `MULTIPLE` | `ANY_OF`、`ALL_OF`、`EXACT_SET` | 非空、不重复的选项 ID 数组。 |
| `NUMBER` | `EQ`、`GT`、`GTE`、`LT`、`LTE` | 一个十进制字符串。 |
| `NUMBER` | `BETWEEN` | `[下界字符串, 上界字符串]`，含边界。 |
| `TEXT` | 无 | 不支持条件跳转，规则只能为空。 |

数字题跳转示例：

```json
{
  "rules": [
    {"priority": 1, "operator": "LT", "value": "18", "target": "END"}
  ]
}
```

题目被其他跳转引用时，删除返回 409 `QUESTION_REFERENCED`；选项被本题规则引用时，删除返回 409 `OPTION_REFERENCED`。重新排序也必须保持跳转向后，否则返回 422。草稿允许暂时没有题目或选项，发布时执行完整检查。

## 6. 填写和提交接口（5 个）

| 方法与路径 | 输入 | 成功结果 |
|---|---|---|
| `GET /public-surveys/{publicCode}` | 分享标识。 | 200：公开填写信息，字段见下文。 |
| `POST /public-surveys/{publicCode}/attempts` | `{}`，附 CSRF。 | 201：AttemptView、绑定 `definition` 与填写 ETag。 |
| `GET /attempts/{attemptId}` | 本人填写 ID。 | 200：AttemptView、绑定 `definition` 与填写 ETag。 |
| `PUT /attempts/{attemptId}/answers` | `{answers: [...]}`，附 CSRF、填写 If-Match。 | 200：AttemptView、`removedQuestionIds` 与新 ETag。 |
| `POST /attempts/{attemptId}/submit` | `{}`，附 CSRF、填写 If-Match、Idempotency-Key。 | 首次 201，重放 200：`{responseId, submittedAt, replayed}`。 |

公开填写信息包含 `publicCode`、`publishedVersion`、`definition`、`deadlineAt`、`serverNow`、`hasSubmitted`、`canStart`、`resumeAttemptId`。最后一项为最近一个未提交尝试的 ID，没有则为 `null`；开始填写接口每次新建尝试，不自动复用该 ID。

保存答案采用**全量替换**，不能只发送改动的一题。以下占位 ID 须替换为绑定定义中的实际 ID：

```json
{
  "answers": [
    {"questionId": "q_choice_placeholder", "value": "opt_placeholder"},
    {"questionId": "q_number_placeholder", "value": "20"},
    {"questionId": "q_text_placeholder", "value": ""}
  ]
}
```

每项必须有 `questionId`、`value`，可选 `type`；提供的题型必须匹配。服务端从第一题重算路径，清除不在有效前缀上的答案并在 `removedQuestionIds` 中告知。未知题目、重复题目、路径内非法答案返回 422。

缺失答案或必答题为空可以保存为未完成状态，`nextQuestionId` 指向待处理题；可选题也须提供显式空答案才表示跳过。只有有效路径完整时才能提交。未发布、已关闭或已到截止时间返回 409 `NOT_COLLECTING`；不允许重复填写且已提交过时，开始或提交返回 409 `ALREADY_SUBMITTED`。

提交成功后本次尝试不可再编辑。使用新键再次提交同一尝试返回 409 `ATTEMPT_SUBMITTED`；再次填写须在策略允许时新建尝试。提交响应没有新的 ETag，若需读取最终状态，可 GET 填写详情。

开始、保存、提交分别按账号限制每分钟 60、180、60 次。

## 7. 统计接口（3 个）

以下接口均仅限问卷创建者。

| 方法与路径 | 查询参数 | 成功结果 |
|---|---|---|
| `GET /surveys/{surveyId}/statistics` | 可选 `publishedVersion` 或 `versionCursor`。 | 200：问卷统计视图。 |
| `GET /surveys/{surveyId}/questions/{questionId}/statistics` | 同上。 | 200：同一统计结构，每个版本只保留指定题目。 |
| `GET /surveys/{surveyId}/questions/{questionId}/answers` | 可选 `publishedVersion`、`cursor`、`limit`。 | 200：`{items, nextCursor}`，仅支持 TEXT、NUMBER。 |

### 7.1 统计视图

顶层为 `{submittedCount, versions, generatedAt, nextVersionCursor}`，各版本包含 `{publishedVersion, title, submittedCount, questions}`。

各题共同字段为 `{questionId, type, title, reachedCount, answeredCount}`。选择题另有 `options: [{optionId, label, count}]`；数字题另有 `average`。

- `submittedCount` 是按账号去重后的有效提交人数；单题接口中的同名字段仍是问卷人数，不是该题作答人数。
- `reachedCount` 为到达该题人数，`answeredCount` 为非空作答人数。多选题每个选项各计选择人数，人数之和可以大于答题人数。
- 数字平均值只统计非空有效数字，按四位小数四舍五入后去尾零，输出字符串；没有有效数字时为 `null`。
- 默认列出较新的发布版本，并按版本分页；顶层人数不随版本翻页缩减。未指定版本时，人数覆盖所有账号的最新有效提交；指定版本后，只计最新答卷恰好属于该版本的人。
- 没有任何发布快照时，问卷概览返回 `submittedCount: 0`、`versions: []`。

### 7.2 填空答案列表

每条记录包含 `publishedVersion`、`value`、`submittedAt`、`anonymous`。非匿名时另有 `respondent: {username}`；匿名时整个 `respondent` 字段省略，不返回账号 ID、参与标识、填写 ID 或答卷 ID。数字 `value` 仍为字符串。

列表先选每账号的最新整份答卷，再过滤题目、版本和空值，按接纳时间与内部 ID 倒序分页。一个账号的新答卷跳过某题后，不会从旧答卷补回该题的旧答案。

## 8. 常见失败响应

| HTTP | 典型错误码 | 处理方式 |
|---|---|---|
| 400 | `BAD_JSON`、`CURSOR_INVALID`、`PAGE_INVALID`、`VERSION_INVALID`、`IDEMPOTENCY_REQUIRED` | 修正 JSON、参数、游标或幂等键。 |
| 401 | `UNAUTHENTICATED`、`LOGIN_FAILED` | 登录或重新认证。 |
| 403 | `CSRF_INVALID`、`ORIGIN_DENIED` | 更新 CSRF，核对访问来源与配置。 |
| 404 | `NOT_FOUND` | 资源不存在或当前账号无权访问。 |
| 409 | `VERSION_CONFLICT`、`NOT_COLLECTING`、`ALREADY_SUBMITTED`、`ATTEMPT_SUBMITTED`、`IDEMPOTENCY_CONFLICT`、`BUSY` 等 | 根据错误刷新版本、停止提交或保留原键重试。 |
| 422 | `FIELDS_INVALID`、`FIELD_INVALID`、`RULES_INVALID`、`ANSWER_REQUIRED`、`NUMBER_INVALID` 等 | 修正字段、配置或答案，参考 `details` 定位。 |
| 428 | `PRECONDITION_REQUIRED` | 获取最新 ETag 后提交 If-Match。 |
| 429 | `RATE_LIMITED` | 降低请求频率。 |
| 503 | `COMMIT_UNCERTAIN`、`DATABASE_UNAVAILABLE` | 对提交请求保留原键及原请求信息重试。 |

框架层的无效方法、路径或超大请求也可能返回 `HTTP_ERROR`，如 405、413。

## 9. 当前实现与设计文档的差异

1. 发布时间存于 `survey_versions.publishedAt`，目前管理和填写 API 都不返回 `publishedAt`；接口提供的 `updatedAt` 不能替代发布时间。
2. 仅修改截止时间时，当前代码增加 `controlRevision`，不增加 `definitionVersion`；题目、标题、描述和策略编辑才增加定义版本。数据库设计文档对截止时间也增加定义版本的文字与实现不同。
3. 当前发布检查要求选择题至少 1 个选项，未强制架构设计中建议的至少 2 个选项。
4. 统计及答案分页先解析游标再验证问卷所有权。跨账号复用游标会先返回 400 `CURSOR_INVALID`，不能将所有越权组合一律写成 404；这也是现有失败用例 TC07-A25 的实际行为，本次未修改。
5. 未另行映射的 MongoDB 驱动错误统一返回 503 `DATABASE_UNAVAILABLE`，包括数据库级文档校验错误；不能将其统称为业务 422。
