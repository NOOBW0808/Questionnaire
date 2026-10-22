# 在线问卷系统测试提交材料

[仓库首页](../README.md) · [被测代码与启动说明](../code/README.md) · [四份文档](../documents/README.md)

本目录包含八个业务模块的测试用例、人工执行记录、API 自动化源码、历史 Allure 报告及原始证据。每个用例列出前置条件、步骤、输入、预期输出、实际输出和判定。

## 1. 文件结构与阅读顺序

```text
test/
├─ README.md
├─ run_api_tests.py              组合仓库的 API 复跑入口
├─ requirements.txt             复用同级代码依赖并安装测试依赖
├─ 测试步骤/
│  ├─ README.md
│  ├─ 01_创建问卷.md … 08_时间信息、收集状态与版本绑定.md
│  ├─ 原始规格/                 READ_TEST.md、cases.json
│  └─ 自动化脚本/               原测试源码；conftest 已适配同级代码
└─ 执行记录/
   ├─ 汇总.md
   ├─ 合并实际结果.json
   ├─ 证据索引.json
   ├─ API执行日志.txt
   ├─ 校验记录/
   └─ Allure/
      ├─ 报告/
      └─ 原始结果/
```

先阅读[测试步骤目录](测试步骤/README.md)，再查看[结果汇总](执行记录/汇总.md)。历史批次 `20261021-182532-ef0f52` 共 143 条记录：137 通过、4 失败、2 跳过；其中手动 13 条、API 130 条。原始用例目录有 142 项（13 手动、129 API），另有 1 条多账号 API 补充记录。本次目录组合保留历史判定，不将路径核对视为重新执行全部业务测试。

## 2. 查看已提交的 Allure 报告

在本仓库根目录 `questionnaire-github/` 打开 PowerShell：

```powershell
python -m http.server 8088 --bind 127.0.0.1 --directory './test/执行记录/Allure/报告'
```

浏览器访问 <http://127.0.0.1:8088>。停止时按 Ctrl+C；端口占用可换 8089。查看历史报告不需要启动 MongoDB、安装 pytest 或安装 Allure CLI；不要直接双击 index.html。

如果终端位于本 README 所在的 `test/`，改用：

```powershell
python -m http.server 8088 --bind 127.0.0.1 --directory './执行记录/Allure/报告'
```

Allure 的 Overview 汇总状态，Suites 按模块分类，用例详情提供截图和请求响应。原始结果位于[执行记录/Allure/原始结果](执行记录/Allure/原始结果/)。

## 3. 在本组合仓库复跑 API 测试

在仓库根目录执行；需要 Python 3.11+ 和已运行的 MongoDB 7.0+ 可写副本集。数据库启动命令见[代码 README](../code/README.md)。测试使用随机的 `questionnaire_test_*` 库和随机 HTTP 端口，默认结束后删除本轮随机测试库，不使用应用业务库。

```powershell
python -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r test/requirements.txt
# 只检查收集和导入，不连接 MongoDB：应收集到 130 项。
& './.venv/Scripts/python.exe' test/run_api_tests.py --collect-only
# 执行 API 测试，连接地址按实际副本集修改。
& './.venv/Scripts/python.exe' test/run_api_tests.py --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
```

入口自动定位 `../code`，加载原始用例目录；可从任意工作目录调用。结果写入本目录 `runs/<新批次>/`，含 pytest.log、junit.xml、runtime.json 和 allure-results，复跑不会覆盖已提交的历史结果。

如已安装 Java 与 Allure 2 CLI，可在执行命令末尾添加 `--allure 'C:/Tools/allure/bin/allure.bat'`，生成该新批次的 API 报告。默认只保存 Allure 原始结果，不要求安装 CLI。新批次只包含 API 测试，人工测试需重新操作并记录，不能沿用旧人工结果冒充本轮已执行。

`测试步骤/自动化脚本/test/run_api_tests.py` 和 `open_report.py` 保留为历史工具快照；其原合并流程需要未随本包提交的原始人工导出 ZIP，不作为本组合仓库入口。请使用上述新增入口。

## 4. 证据与限制

`执行记录/证据索引.json` 内路径以 `test/` 为基准；合并实际结果中附件 source 以 `执行记录/Allure/原始结果/` 为基准。历史 JSON、报告、截图和日志保持原字节；其中出现的旧绝对路径属于来源元数据，不是本仓库运行配置。

TC06-M01 保留跳过，多账号计数与分页由 TC06-M01-AUTO 补充，页面可读性未代验；TC08-A05 因无法精确控制 MongoDB 时钟恰等截止时间而跳过。TC07-A25 的 400/404 差异与三项人工失败均保留；未将失败改为通过。
