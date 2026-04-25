# Запустить от имени администратора!
$batPath = "d:\вайбкодинг\напоминание о парах\start_bot.bat"

$action = New-ScheduledTaskAction -Execute $batPath

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable $true

Register-ScheduledTask `
    -TaskName "TGU Reminder Bot" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Готово! Бот будет запускаться автоматически при входе в Windows." -ForegroundColor Green
