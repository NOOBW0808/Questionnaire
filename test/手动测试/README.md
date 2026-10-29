# 手动测试

[网页](网页/)用于记录人工操作的步骤结果、实际表现及截图；[原始导出](原始导出/)保留manual-results.zip与allure-results.zip，两者是同一批人工证据的不同导出，不重复计数。

在仓库根目录启动：

```powershell
& './code/.venv/Scripts/python.exe' test/工具/manual_server.py
```

浏览器打开终端打印的地址。网页不代替操作被测系统；完成页面交互后如实记录。新批次状态在本地`.local/phase2-manual`保存，不提交Git。合并及查看方法见[工具说明](../工具/README.md)。
