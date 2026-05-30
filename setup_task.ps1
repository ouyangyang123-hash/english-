# Windows 定时任务安装脚本
# 每天 6:00 AM 自动执行英语 Vlog 抓取
# 使用方法：以管理员身份运行 PowerShell，执行此脚本

$taskName = "DailyEnglishVlogFetch"
$scriptPath = "C:\Users\Tourism\Desktop\助理团队\english-repo\daily_vlog_fetch.py"
$pythonPath = "C:\Users\Tourism\miniconda3\python.exe"
$workingDir = "C:\Users\Tourism\Desktop\助理团队\english-repo"

# 删除已存在的同名任务
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task: $taskName"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# 创建任务操作：运行 Python 脚本
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $workingDir

# 创建任务触发器：每天 6:00 AM
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "06:00AM"

# 创建任务设置
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

# 注册任务（以当前用户身份运行）
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "每天6:00自动搜索微信公众号英语Vlog文章，保存到本地"

Write-Host ""
Write-Host "============================================"
Write-Host "  Task '$taskName' created successfully!"
Write-Host "  Schedule: Daily at 6:00 AM"
Write-Host "  Script : $scriptPath"
Write-Host "============================================"
Write-Host ""
Write-Host "To test now, run:"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "To check status:"
Write-Host "  Get-ScheduledTask -TaskName '$taskName'"
