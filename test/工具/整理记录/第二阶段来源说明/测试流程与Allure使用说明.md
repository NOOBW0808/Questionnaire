> GitHub路径补充：当前从仓库根目录操作，虚拟环境可在code/.venv或仓库根.venv；命令中的本机Allure路径须自行替换。查看已提交报告无需Allure CLI；原始报告和人工来源不重新判定。

# 第二阶段：运行API并合并手工Allure

## 当前测试范围

当前目录v4共213项：**22项手工＋191项API**。第一阶段回归130项API，第二阶段61项API（原43＋本轮新增18），没有待编写占位项。

按用户要求，从当前手工用例、`cases.json`及对应Markdown中移除最后七项：`TC14-M02、TC15-M01、TC16-M01、TC20-M01～M04`；其中页面整链模块已无当前用例，因此删除其当前规格文件。原ZIP、已保存的人工原批次、第一阶段原始规格和历史报告保持原状。删除的是当前测试范围，不能把这七项说成已经测试或通过。摘要、统计、权限等业务仍由API验证。

你提供的 `manual-results.zip` 与 `allure-results.zip` 中，22项保留用例均由你标记为通过，共47张截图；实际现象保持原文。两份导出中状态、文字和截图哈希逐项核对一致。合并时注明“用户原批次记录，未由自动化重测”，不伪造新的人工执行时间。

## 1. 你自行一键运行

在仓库根目录打开PowerShell：

```powershell
& './test/一键运行与查看.ps1'
```

此命令校验你放在 `test/手动测试/原始导出/` 下的两份ZIP，导入22项手工结果，重新跑130项旧API和61项第二阶段API，生成新Allure，然后启动HTTP服务并自动打开浏览器，默认地址 **http://127.0.0.1:5083**。保持终端运行，Ctrl+C停止报告服务。

MongoDB 7+可写副本集须已启动；脚本在随机隔离库运行，默认清理本轮专用库，不使用你的手工测试库，不修改真实旧库。无需先启动5000问卷服务，自动化会自行启动随机HTTP端口。

若PowerShell限制本地脚本执行，直接使用下面的Python命令即可，无需更改系统执行策略。Allure依赖可用Java，本机路径已确认；路径不同可指定：

```powershell
& './test/一键运行与查看.ps1' -AllureCli 'D:/allure-2.45.0/bin/allure.bat' -MongoUri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs' -Port 5083
```

## 2. 分两步运行（建议初次了解时使用）

先执行API并合并：

```powershell
& './code/.venv/Scripts/python.exe' -X utf8 'test/工具/run_allure_cycle.py' --manual-zip 'test/手动测试/原始导出/manual-results.zip' --manual-allure 'test/手动测试/原始导出/allure-results.zip' --mongo-uri 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs' --allure 'D:/allure-2.45.0/bin/allure.bat'
```

再查看最新报告：

```powershell
& './code/.venv/Scripts/python.exe' -X utf8 'test/工具/view_allure.py' --port 5083
```

浏览器打开 **http://127.0.0.1:5083**。`view_allure.py`读取 [最新合并报告.json](../../../执行记录/完整系统回归/来源最新合并报告.json)，避免手动寻找目录；它只显示已生成的报告，不重跑测试。不要双击Allure的index.html。

每次API执行都建立新轮次，绝不直接沿用旧报告通过。因真实断言失败返回1时仍生成报告，一键脚本仍会打开；参数、证据或生成错误返回2时终止，不打开旧报告冒充本轮。`complete=true`表示本范围都有结果，`allPassed=false`仍表示存在失败或跳过，两者不要混淆。

需要安装依赖时：

```powershell
& './code/.venv/Scripts/python.exe' -m pip install -r test/requirements.txt
```

Allure CLI不属于pip依赖；没有CLI时去掉 `--allure` 可保存JSON、JUnit和原始Allure结果，但没有HTML预览。

## 3. 只跑部分API

```powershell
# 收集当前第二阶段61项，不连接数据库。
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --collect-only
# 只跑本轮新增的18项。
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py --batch extended
# 跑全部第二阶段61项，不自动合并手工报告。
& './code/.venv/Scripts/python.exe' test/工具/run_phase2_tests.py
```

原 `--batch 1/2/3/transactions/migration` 保留原范围，新增18项通过extended或all执行；子集通过不代表全部通过。完整混合报告应使用上一节的 `run_allure_cycle.py`。

新增脚本见 [自动化脚本](../../../自动化脚本/第二阶段/README.md)，用例映射见 [用例目录](../../../测试步骤/第二阶段/README.md)。API以真实HTTP请求执行业务；只在隔离测试库做持久化核对、关联故障注入和升级演练。没有改业务实现或第一阶段原脚本。

## 4. 手工结果如何合并

导入器验证ZIP安全路径、唯一caseId、截图大小和SHA256，并交叉核对两份导出。只复用与你原批次**业务代码哈希相同、保留手工规格逐项相同**的22项；API脚本新增不改变这些手工规格。若业务实现变了则拒绝直接导入，要求重测，不提供绕过参数。

移除七项仅记入导入审计，不再出现在新报告的测试项/跳过计数里。原29项ZIP不会被覆盖；API结果包里的历史手工JSON只用于交叉验证，不直接复制为新自动化结果。

每次导入生成新收集批次，保存来源runId、原时间、源ZIP哈希和排除原因。命令会打印 `IMPORTED MANUAL RUN` 与 `COMBINED` 目录；运行凭据见 [最新合并报告.json](../../../执行记录/完整系统回归/来源最新合并报告.json)。当前实现/用例/脚本指纹不匹配时，不能把别轮API混进来。

要继续使用手工收集页：

```powershell
& './code/.venv/Scripts/python.exe' 'test/工具/manual_server.py' --port 5081
```

打开 http://127.0.0.1:5081，当前只显示目录v4的22项手工用例，旧规格批次隐藏但原文件仍保存。可选择导入后的“已完成手工22项＋新一轮业务API”批次核对47张截图。通过/失败仍需截图及实际文字；保存后的新人工记录不会自动刷新旧Allure。

## 5. 报告怎样查看

- **Suites**：第一阶段回归 / 第二阶段增量，再按业务模块与手工/API分组。
- **Categories**：新增失败 / 原有失败 / 阻塞。
- **Behaviors**：新增失败、原有失败、已修复项、跳过项、保持通过、新增通过。
- 手工个例的Description显示实际现象，Attachments有原截图、完整用例、人工来源和基线对比。
- API个例有本轮真实请求/响应（凭据脱敏）、断言详情与执行阶段；日志和JUnit在同轮目录保留。

第一阶段原143条基线保持原样：137通过、4失败、2跳过。基线只用来分类，不填入本轮通过。原三项手工失败在你的新人工记录里为通过，因此按规则显示已修复3项；这仅是相同用例两批记录的状态变化，不表示本轮修改了业务代码，也不表示截图证明了未操作的全部分支。

Allure默认通过率排除skipped，不能将其视为全范围验收率；以实际结果JSON的状态数量、失败详情和测试边界为准。原 `TC08-A05` 若因精确截止等号条件无法可靠执行仍明确跳过，不伪造服务端时钟。

## 6. 输出及保留

```text
test/执行记录/完整系统回归/本地运行/allure-cycle-<新时间>-<随机值>/
├─ cycle.json、catalog.json、baseline.json
├─ phase1/、phase2/   pytest.log / junit.xml / observations.json / allure-results/
└─ combined-<新时间>-<随机值>/
   ├─ actual-results.json                本轮213项
   ├─ baseline-comparison-only.json      原始基线，仅供比较
   ├─ comparison.md                      分类与当前失败清单
   ├─ allure-results/                    本轮API＋22项手工/截图
   └─ allure-report/                     可通过HTTP查看的报告
```

原导出ZIP和历史执行目录不覆盖。当前文件清单见 [本轮文件清单](API补全与人工合并文件清单.json)，实际结果与已知缺陷见 [本轮记录](../../../执行记录/第二阶段/20261029-API补全与人工合并/README.md)。
