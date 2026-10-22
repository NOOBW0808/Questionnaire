# 文档阅读指南

[仓库首页](../README.md) · [代码运行](../code/README.md) · [测试与证据](../test/README.md)

四份正式说明文档按“系统 → 数据 → 接口 → 关键逻辑”组织。本 README 仅为导航，不计入四份正式说明文档。

| 文档 | 主要内容 | 关联实现和验证 |
|---|---|---|
| [系统说明](系统说明.md) | 功能、身份权限、结构、操作流程 | [后端模块](../code/survey_app/)、[页面](../code/static/)、[业务测试目录](../test/测试步骤/README.md) |
| [数据库设计](数据库设计.md) | 八集合、嵌入与引用、索引、版本和事务 | [schemas.json](../code/数据库设计/schemas.json)、[数据库访问](../code/survey_app/database.py) |
| [API说明](API说明.md) | 23 个业务接口、请求响应、错误码与实现差异 | [认证](../code/survey_app/security.py)、[管理](../code/survey_app/management.py)、[填写](../code/survey_app/filling.py)、[统计](../code/survey_app/statistics.py) |
| [关键逻辑说明](关键逻辑说明.md) | 版本隔离、跳转、校验、并发、幂等及最新整卷统计 | [规则代码](../code/survey_app/domain.py)、[测试结果](../test/执行记录/汇总.md) |

