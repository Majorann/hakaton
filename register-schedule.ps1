param([switch]$Preview, [switch]$Replace)

$ErrorActionPreference = 'Stop'
$schedule = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'schedule.psd1')
$time = [datetime]::ParseExact($schedule.DailyAt, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$program = Join-Path $PSScriptRoot 'vacancy_report.py'
$log = Join-Path $PSScriptRoot 'output/schedule.log'
$arguments = '"{0}" --log-file "{1}" api' -f $program, $log

if ($Preview) {
    [pscustomobject]@{Task = $schedule.TaskName; DailyAt = $schedule.DailyAt; Program = $python; Arguments = $arguments}
    return
}

$trigger = New-ScheduledTaskTrigger -Daily -At $time
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName $schedule.TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force:$Replace
