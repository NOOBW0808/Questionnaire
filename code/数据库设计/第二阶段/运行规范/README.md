# 第二阶段运行规范

本目录为已确认P2-1.1方案的**运行副本**。第一阶段`../../schemas.json`保留原字节。应用对空专用库安装本目录18集合；对完整旧库只进入兼容模式，不执行升级。未知结构、漂移索引或升级中间态阻断启动。

- `schemas.json`：包含validator和索引的运行权威。等价移除MongoDB拒绝的空`required: []`，补充题库创建幂等用例CREATE_BANK及最小bankId结果字段。设计依据见[数据库设计说明](../../../../documents/数据库设计/第二阶段/数据库设计.md)。
- `indexes.json`：设计索引目录，与运行schema的索引一致；不独立执行。
- `transaction_boundaries.json`：已确认事务边界；实际执行由Python事务封装实现。
- `usage_summary_policy.json`：SUMMARY_V1策略；HTTP只输出允许字段。
- `migration_plan.json`：运行计划元数据及设计来源哈希，不代表实际业务库已迁移。迁移工具额外计算规范EJSON计划哈希并写入备份/台账。
- `schema_preview.js`：可选Node只读审查工具，输出新增集合及索引，不连接数据库。运行应用不需要Node。

显式升级入口：`../../../scripts/phase2_schema.py`。完整停写、备份、恢复副本演练、升级、验证、激活及激活前回退步骤见[README_PHASE2](../../../README_PHASE2.md)。备份和台账归档存本地`.local/`，禁止push。DDL不能包在一个事务中；真实使用投影、迁移项和检查点按源对象同事务防重，绝不按旧题干推断来源。
