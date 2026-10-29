# 测试工具

| 入口 | 用途 |
| --- | --- |
| run_api_tests.py | 第一阶段API回归 |
| run_phase2_tests.py | 第二阶段API与指定批次 |
| manual_server.py | 手动测试结果收集服务 |
| run_allure_cycle.py | 执行两阶段API并合并人工证据 |
| view_allure.py | 通过HTTP打开最新完整报告 |
| test_workbench/ | 规格加载、结果采集、合并及工具自检 |

推荐命令见[测试首页](../README.md)。历史工具和整理记录仅供追溯，不作为当前运行入口。原始用例目录和证据ZIP保持来源内容；本次仅调整路径和入口配置。

```powershell
# 仓库根目录执行：重跑API，导入历史人工证据；未指定Allure时仅生成原始结果。
& './code/.venv/Scripts/python.exe' test/工具/run_allure_cycle.py --manual-zip test/手动测试/原始导出/manual-results.zip --manual-allure test/手动测试/原始导出/allure-results.zip --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
```
