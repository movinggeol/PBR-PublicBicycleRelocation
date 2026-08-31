<#
.SYNOPSIS
    TMAP 실도로 소요시간 반복 수집기 운영 — 하루 한 번 (docs/분석/EXPERIMENTS.md 9장).

.DESCRIPTION
    재고 수집기(collector.ps1)와 달리 **하루 한 번**만 돌면 된다. TMAP의
    `startTime`이 교통량을 정하므로, 언제 호출하든 같은 시각의 교통량을 받는다.

    **평일에만 깨운다.** `start_time_for()`가 '다음 평일'을 쓰기 때문에 금·토·일에
    돌리면 셋 다 '다음 월요일'을 가리켜 같은 값이 세 번 쌓인다.

    호출은 하루 20건(사슬 5 × 회차 4)이다. 재고 수집기와 달리 TMAP 유료 API를
    쓰므로 간격을 좁히지 마라 — 일일 한도를 파이프라인 실행분과 나눠 쓴다.

.EXAMPLE
    .\scripts\road_collector.ps1 install    # 매 평일 03:30 수집 시작
    .\scripts\road_collector.ps1 install -At 04:00
    .\scripts\road_collector.ps1 status     # 스케줄 + 쌓인 현황
    .\scripts\road_collector.ps1 now        # 지금 한 번 즉시 수집
    .\scripts\road_collector.ps1 pause      # 일시정지
    .\scripts\road_collector.ps1 resume
    .\scripts\road_collector.ps1 uninstall  # 작업 삭제 (데이터는 남는다)

.NOTES
    데이터를 지우는 명령은 없다. 일시정지·중지 모두 스케줄만 건드린다.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'pause', 'resume', 'uninstall', 'status', 'now')]
    [string]$Command = 'status',

    # 수집 시각. 새벽으로 잡는 이유는 TMAP 일일 한도를 파이프라인 실행보다
    # 먼저 조금 떼어 두기 위해서다(한도는 자정에 풀린다).
    [string]$At = '03:30'
)

$ErrorActionPreference = 'Stop'

$TaskName = 'PBR도로시간수집'
$Root     = Split-Path -Parent $PSScriptRoot
$PythonW  = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Python   = Join-Path $Root '.venv\Scripts\python.exe'
$Script   = Join-Path $Root 'tools\collect_road_time.py'


function Format-Stamp {
    <# 작업 스케줄러는 '한 번도 안 돎'을 1999-11-30으로 돌려준다. #>
    param($Value)
    if ($null -eq $Value) { return $null }
    $stamp = [datetime]$Value
    if ($stamp.Year -lt 2000) { return $null }
    $stamp.ToString('yyyy-MM-dd HH:mm')
}

function Format-Result {
    param([int]$Code)
    switch ($Code) {
        0      { '성공' }
        1      { '수집 실패 (한도 초과 등 — now로 손수 돌려 확인)' }
        267011 { '아직 실행 전' }
        267009 { '실행 중' }
        267014 { '중지됨' }
        default { "코드 $Code" }
    }
}

function Get-Task {
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

function Invoke-Install {
    Assert-Paths
    $when = [datetime]::ParseExact($At.Trim(), 'HH:mm', $null)

    $action = New-ScheduledTaskAction -Execute $PythonW `
        -Argument ('"{0}"' -f $Script) -WorkingDirectory $Root

    # 평일만. 주말에 깨워 봐야 '다음 월요일'을 세 번 재게 된다.
    $trigger = New-ScheduledTaskTrigger -Weekly -At $when `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday

    # StartWhenAvailable을 **켠다** — 재고 수집기와 반대다. 저쪽은 10분 뒤 다음
    # 틱이 오지만 이쪽은 하루 한 번뿐이라, PC가 꺼져 있었다면 그날을 통째로 잃는다.
    # 하루치를 늦게 채우는 것은 격자를 덮어쓰지 않는다(라벨이 날짜 단위다).
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force `
        -Description "TMAP 고정 패널 실도로 소요시간을 매 평일 $At 에 수집합니다 (docs/분석/EXPERIMENTS.md 9장)." | Out-Null

    Write-Host "[등록] $TaskName — 평일 $At · 하루 TMAP 20호출" -ForegroundColor Green
    Write-Host "  다음 실행: $(Format-Stamp (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
    Write-Host "  현황 보기: .\scripts\road_collector.ps1 status"
}

function Invoke-Pause {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다. install부터 하세요."; return }
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "[일시정지] $TaskName — 작업은 남아 있고 깨우지 않습니다." -ForegroundColor Yellow
}

function Invoke-Resume {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다. install부터 하세요."; return }
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "[재개] $TaskName" -ForegroundColor Green
    Write-Host "  다음 실행: $(Format-Stamp (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
}

function Invoke-Uninstall {
    if (-not (Get-Task)) { Write-Warning "등록된 작업이 없습니다."; return }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[중지] $TaskName 작업을 삭제했습니다." -ForegroundColor Yellow
    Write-Host "  이미 모은 데이터는 지우지 않았습니다."
}

function Invoke-Status {
    Assert-Paths
    $task = Get-Task
    if (-not $task) {
        Write-Host "  스케줄   : 등록되지 않음 (.\scripts\road_collector.ps1 install)" -ForegroundColor DarkGray
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $state = if ($task.State -eq 'Disabled') { '일시정지' } else { '활성' }
        Write-Host "  스케줄   : $state · 다음 실행 $(Format-Stamp $info.NextRunTime)"
        $last = Format-Stamp $info.LastRunTime
        if ($null -eq $last) {
            Write-Host "  마지막   : 아직 실행 전"
        } else {
            Write-Host "  마지막   : $last · $(Format-Result $info.LastTaskResult)"
        }
    }
    & $Python $Script --status
}

function Invoke-Now {
    Assert-Paths
    Write-Host "[즉시 수집] TMAP 20호출을 지금 보냅니다." -ForegroundColor Cyan
    & $Python $Script
}


switch ($Command) {
    'install'   { Invoke-Install }
    'pause'     { Invoke-Pause }
    'resume'    { Invoke-Resume }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
    'now'       { Invoke-Now }
}
