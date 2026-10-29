# 完整系统测试材料

本目录按材料类型组织，两阶段的用例、脚本和执行证据分别保存。第一阶段回归确认原功能继续工作，第二阶段测试验证新增功能；目录调整不改变历史判定。

| 目录 | 内容 |
| --- | --- |
| [测试步骤](测试步骤/README.md) | 第一阶段01～08模块、第二阶段09～19模块的步骤、输入与预期 |
| [自动化脚本](自动化脚本/README.md) | 第一阶段130项、第二阶段61项API测试 |
| [手动测试](手动测试/README.md) | 结果收集网页、原始导出ZIP及使用方法 |
| [执行记录](执行记录/README.md) | 第一阶段历史、第二阶段历史、完整系统回归记录 |
| [工具](工具/README.md) | 自动化入口、结果合并、报告查看及整理过程归档 |

## 运行自动化测试

以下命令在仓库根目录执行。先按[代码说明](../code/README_PHASE2.md)创建Python环境并启动MongoDB可写副本集；测试只操作随机隔离测试库，不迁移业务库。

```powershell
& './code/.venv/Scripts/python.exe' -m pip install -r test/requirements.txt
# 只收集，不连接数据库：分别130项、61项。
& './code/.venv/Scripts/python.exe' test/工具/run_api_tests.py --collect-only
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --collect-only
# 实际执行；按本机情况调整MongoDB连接地址。
& './code/.venv/Scripts/python.exe' test/工具/run_api_tests.py --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs'
```

新结果写入`执行记录/完整系统回归/本地运行/`，默认不提交Git，不覆盖历史记录。原人工记录有22项、47张截图；导入历史人工记录不代表本轮重新点击过页面。

## 查看报告或运行完整流程

```powershell
# 查看已提交的完整历史HTML报告，不执行测试。
& './code/.venv/Scripts/python.exe' test/工具/view_allure.py --port 5083
# 重跑191项API并合并原始人工证据，生成新HTML；需要本机Allure CLI和Java。
& './test/一键运行与查看.ps1' -AllureCli 'C:/Tools/allure/bin/allure.bat'
```

已记录的完整回归：第一阶段128通过、1失败、1跳过；第二阶段59通过、2失败。保留非法ID返回500及游标400/404差异，不将历史失败改为通过。详见[提交整理验证](执行记录/完整系统回归/提交整理验证/README.md)。本轮目录调整的检查另见[目录调整验证](执行记录/完整系统回归/目录调整验证.json)。

[仓库首页](../README.md) · [系统文档](../documents/README.md)
