<#
.SYNOPSIS
    타슈 재고 시계열 수집기 운영 — 시작·일시정지·중지 (docs/구현/COLLECTOR.md).

.DESCRIPTION
    Windows 작업 스케줄러 등록을 동사 하나로 감싼다. schtasks.exe 대신
    Register-ScheduledTask를 쓰는 이유는 아래 설정 절반이 schtasks 플래그로
    표현되지 않기 때문이다(동시 실행 억제, 실행 시간 제한, 놓친 작업 따라잡기 끄기).

    실행 파일로 pythonw.exe를 쓴다 — python.exe로 걸면 10분마다, 하루 49번
    콘솔 창이 깜빡인다.

.EXAMPLE
    .\scripts\collector.ps1 install     # 수집 시작 (최초 1회 등록)
    .\scripts\collector.ps1 pause       # 일시정지 — 작업은 남기고 안 깨움
    .\scripts\collector.ps1 resume      # 재개
    .\scripts\collector.ps1 uninstall   # 완전 중지 — 작업 삭제
    .\scripts\collector.ps1 status      # 스케줄 상태 + 수집 현황
    .\scripts\collector.ps1 now         # 지금 한 틱 즉시 수집

.NOTES
    데이터를 지우는 명령은 없다. 일시정지·중지 모두 스케줄만 건드린다.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'pause', 'resume', 'uninstall', 'status', 'now')]
    [string]$Command = 'status',

    [string]$Window = '09:00-17:00',
    [int]$Interval = 10,

    # 주말·공휴일에도 수집한다. 두 번째 PC가 휴일을 맡는 구성용
    # (docs/구현/COLLECTOR.md 11장). 트리거를 7일로 넓히고 스크립트의 휴일
    # 가드도 함께 푼다 — 둘 중 하나만 풀면 깨워도 안 모으거나 그 반대가 된다.
    [switch]$IncludeHolidays,

    # 로그오프 상태에서도 돌린다. 비밀번호를 저장해야 하므로 기본값이 아니다.
    [switch]$RunWhenLoggedOff
)

$ErrorActionPreference = 'Stop'

$TaskName = 'PBR재고수집'
$Root     = Split-Path -Parent $PSScriptRoot
$PythonW  = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Python   = Join-Path $Root '.venv\Scripts\python.exe'
$Script   = Join-Path $Root 'tools\collect_stock.py'


function Format-Stamp {
    <# 작업 스케줄러는 '한 번도 안 돎'을 1999-11-30으로 돌려준다. #>
    param($Value)
    if ($null -eq $Value) { return $null }
    $stamp = [datetime]$Value
    if ($stamp.Year -lt 2000) { return $null }
    $stamp.ToString('yyyy-MM-dd HH:mm')
}

function Format-Result {
    <# LastTaskResult는 HRESULT다. 그대로 찍으면 정상 상태가 오류처럼 보인다. #>
    param([int]$Code)
    switch ($Code) {
        0      { '성공' }
        1      { '수집 실패 (로그의 detail 확인)' }
        267011 { '아직 실행 전' }        # SCHED_S_TASK_HAS_NOT_RUN
        267009 { '실행 중' }             # SCHED_S_TASK_RUNNING
        267014 { '중지됨' }              # SCHED_S_TASK_TERMINATED
        default { "코드 $Code" }
    }
}

function Get-Task {
    # 등록 전에는 '없음'이 정상이다. CIM 계열은 try/catch로 감싸도 오류 레코드를
    # 먼저 찍으므로, 아예 나오지 않게 SilentlyContinue로 막고 $null로 판단한다.
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Assert-Paths {
    if (-not (Test-Path $PythonW)) {
        throw "가상환경을 찾을 수 없습니다: $PythonW  (프로젝트 규약: .venv를 씁니다)"
    }
    if (-not (Test-Path $Script)) {
        throw "수집기를 찾을 수 없습니다: $Script"
    }
}

function Get-WindowSpan {
    $parts = $Window -split '-'
    if ($parts.Count -ne 2) { throw "수집 창 형식이 잘못됐습니다: '$Window' (예: 09:00-17:00)" }
    $start = [datetime]::ParseExact($parts[0].Trim(), 'HH:mm', $null)
    $end   = [datetime]::ParseExact($parts[1].Trim(), 'HH:mm', $null)
    if ($start -ge $end) { throw "수집 창의 끝이 시작보다 늦어야 합니다: '$Window'" }
    [pscustomobject]@{ Start = $start; Duration = $end - $start }
}

function Test-PowerSettings {
    <#
        절전 설정을 점검해 경고만 한다 — 사용자 시스템 설정을 말없이 바꾸지 않는다.
        powercfg 출력은 지역화돼 있어 문구 대신 16진수 토큰의 **순서**로 읽는다
        (마지막 두 개가 AC, DC 설정값이다).
    #>
    $onBattery = $false
    try {
        $battery = Get-CimInstance Win32_Battery -ErrorAction Stop
        # BatteryStatus 2 = 외부 전원 연결됨
        if ($battery -and $battery.BatteryStatus -ne 2) { $onBattery = $true }
    } catch { }

    $acSeconds = $null
    try {
        $raw = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) -join "`n"
        $hex = [regex]::Matches($raw, '0x[0-9a-fA-F]{8}')
        if ($hex.Count -ge 2) {
            $acSeconds = [convert]::ToInt64($hex[$hex.Count - 2].Value, 16)
        }
    } catch { }

    $span = (Get-WindowSpan).Duration.TotalSeconds
    if ($onBattery) {
        Write-Warning "지금 배터리로 돌고 있습니다. 배터리에서는 절전에 들어가 수집이 끊깁니다 — 전원 어댑터를 꽂아 두세요."
    }
    if ($null -ne $acSeconds) {
        if ($acSeconds -eq 0) {
            Write-Host "  전원 연결 시 절전: 안 함 — 수집 창 내내 깨어 있습니다." -ForegroundColor DarkGray
        } elseif ($acSeconds -lt $span) {
            Write-Warning ("전원 연결 시 {0}분 뒤 절전으로 설정돼 있습니다(수집 창 {1}시간). " -f ($acSeconds / 60), ($span / 3600) +
                           "절전 중에는 수집되지 않습니다 — 전원 옵션에서 절전을 '안 함'으로 두는 것을 권합니다.")
        }
    }
}

function Invoke-Install {
    Assert-Paths
    $span = Get-WindowSpan

    $arguments = '"{0}" --once --window {1} --interval {2}' -f $Script, $Window, $Interval
    if ($IncludeHolidays) { $arguments += ' --include-holidays' }
    $action = New-ScheduledTaskAction -Execute $PythonW -Argument $arguments -WorkingDirectory $Root

    # 스케줄러는 요일만 알고 공휴일을 모른다. 평일 수집이면 주말 트리거를 아예
    # 만들지 않고, 공휴일은 스크립트 가드가 거른다.
    $DayLabel = if ($IncludeHolidays) { '매일(휴일 포함)' } else { '평일' }
    $days = if ($IncludeHolidays) {
        [System.DayOfWeek[]]@('Monday', 'Tuesday', 'Wednesday', 'Thursday',
                              'Friday', 'Saturday', 'Sunday')
    } else {
        [System.DayOfWeek[]]@('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
    }

    # 주간 트리거에는 반복 설정이 노출되지 않는다. 일회성 트리거에서 Repetition만 떼어 옮긴다.
    $trigger = New-ScheduledTaskTrigger -Weekly -At $span.Start -DaysOfWeek $days
    $repeat = New-ScheduledTaskTrigger -Once -At $span.Start `
        -RepetitionInterval (New-TimeSpan -Minutes $Interval) `
        -RepetitionDuration $span.Duration
    $trigger.Repetition = $repeat.Repetition

    # StartWhenAvailable은 켜지 않는다: 놓친 틱을 뒤늦게 실행하면 이미 값이 있는
    # 격자 슬롯을 낡은 값으로 덮어쓴다. 10분 뒤 다음 틱이 오므로 따라잡을 이유가 없다.
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    $register = @{
        TaskName    = $TaskName
        Action      = $action
        Trigger     = $trigger
        Settings    = $settings
        Description = "타슈 대여소 재고를 $DayLabel $Window, ${Interval}분 간격으로 수집합니다 (docs/구현/COLLECTOR.md)."
        Force       = $true
    }
    if ($RunWhenLoggedOff) {
        $credential = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" `
            -Message '로그오프 상태에서도 수집하려면 Windows 로그인 비밀번호가 필요합니다.'
        $register.User = $credential.UserName
        $register.Password = $credential.GetNetworkCredential().Password
        $register.RunLevel = 'Limited'
    }

    Register-ScheduledTask @register | Out-Null
    Write-Host "[등록] $TaskName — $DayLabel $Window · ${Interval}분 간격" -ForegroundColor Green
    Test-PowerSettings

    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "  다음 실행: $(Format-Stamp $info.NextRunTime)"
    Write-Host "  현황 보기: .\scripts\collector.ps1 status"
}

function Invoke-Pause {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다. install부터 하세요."; return }
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "[일시정지] $TaskName — 작업은 남아 있고 깨우지 않습니다." -ForegroundColor Yellow
    Write-Host "  이미 모은 데이터는 그대로입니다. 재개: .\scripts\collector.ps1 resume"
}

function Invoke-Resume {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다. install부터 하세요."; return }
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "[재개] $TaskName" -ForegroundColor Green
    Test-PowerSettings
    Write-Host "  다음 실행: $(Format-Stamp (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
}

function Invoke-Uninstall {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다."; return }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[중지] $TaskName 작업을 삭제했습니다." -ForegroundColor Yellow
    Write-Host "  이미 모은 데이터는 지우지 않았습니다. 다시 시작: .\scripts\collector.ps1 install"
}

function Invoke-Status {
    Assert-Paths
    $task = Get-Task
    if (-not $task) {
        Write-Host "  스케줄   : 등록되지 않음 (.\scripts\collector.ps1 install)" -ForegroundColor DarkGray
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $state = if ($task.State -eq 'Disabled') { '일시정지' } else { '활성' }
        Write-Host "  스케줄   : $state · 다음 실행 $(Format-Stamp $info.NextRunTime)"
        $last = Format-Stamp $info.LastRunTime
        if ($null -eq $last) {
            Write-Host "  마지막   : 아직 실행 전 — 첫 수집은 다음 실행 시각입니다"
        } else {
            Write-Host "  마지막   : $last · $(Format-Result $info.LastTaskResult)"
        }
    }
    & $Python $Script --status --window $Window --interval $Interval
}

function Invoke-Now {
    Assert-Paths
    Write-Host "[즉시 수집] 창 밖이어도 한 틱 받아 옵니다." -ForegroundColor Cyan
    & $Python $Script --once --force --window $Window --interval $Interval
}


switch ($Command) {
    'install'   { Invoke-Install }
    'pause'     { Invoke-Pause }
    'resume'    { Invoke-Resume }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
    'now'       { Invoke-Now }
}
