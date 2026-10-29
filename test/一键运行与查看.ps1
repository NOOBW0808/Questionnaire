param(
    [string]$AllureCli = 'D:/allure-2.45.0/bin/allure.bat',
    [string]$MongoUri = 'mongodb://127.0.0.1:27018/?replicaSet=survey-rs',
    [int]$Port = 5083
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $projectRoot 'code/.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少 .venv，请按测试说明安装Python依赖。' }
if (-not (Test-Path -LiteralPath $AllureCli)) { throw 'Allure路径不存在，请通过 -AllureCli 指定实际allure.bat。' }
Push-Location $projectRoot
try {
    & $pythonPath -X utf8 'test/工具/run_allure_cycle.py' --manual-zip 'test/手动测试/原始导出/manual-results.zip' --manual-allure 'test/手动测试/原始导出/allure-results.zip' --mongo-uri $MongoUri --allure $AllureCli
    $apiExitCode = $LASTEXITCODE
    if ($apiExitCode -gt 1) { throw "执行未完成，退出码 $apiExitCode；检查终端报错，未打开旧报告冒充本轮结果。" }
    if ($apiExitCode -eq 1) { Write-Host '本轮有真实失败/异常，报告已保留，不改判通过。' }
    & $pythonPath -X utf8 'test/工具/view_allure.py' --port $Port
}
finally { Pop-Location }
