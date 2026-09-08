<#
.SYNOPSIS
    TMAP 실도로 소요시간 반복 수집기 운영 — 하루 한 번치 (docs/구현/COLLECTOR_ROAD.md).

.DESCRIPTION
    재고 수집기(collector.ps1)와 달리 **하루 한 번치**만 모으면 된다. TMAP의
    `startTime`이 교통량을 정하므로, 언제 호출하든 같은 시각의 교통량을 받는다.
    그래서 "몇 시에 도느냐"가 아니라 "그날 받았느냐"만 중요하다.

    **하루에 여러 번 깨우고, 필요할 때만 부른다** (1.26.105). 수집기에
    `--if-needed`를 붙여 돌리므로

      · 그날 몫(회차 4개 × 100구간)을 이미 받았으면 TMAP을 **한 번도 부르지 않고** 끝난다
      · 한도 소진·네트워크 실패로 잘린 회차가 있으면 **그것만** 채운다
      · day-type을 안 주면 **그날의 실제 요일**로 평일/휴일을 자동 판정한다

    그래서 시각을 여러 개 걸어도 **호출은 하루 20건(사슬 5 × 회차 4)을 넘지 않는다.**
    예전에는 평일 03:30 한 번이었는데, 새벽에 PC를 켜 두지 않는 환경에서는 그날을
    통째로 잃었다(2026-09-03에 실제로 비었다).

    **평일·휴일을 나눠서 잰다 (1.26.158).** `start_time_for()`가 `day_type`을
    받아 평일이면 '다음 평일', 휴일이면 '다음 토·일 또는 공휴일'을 만든다 —
    같은 요일로 밀어야 다른 날로 셀 수 있다. 결과는 `runs.day_type`에 남고
    `road_time_model.py --day-type`이 그걸로 갈라 회귀한다.

.EXAMPLE
    .\scripts\road_collector.ps1 install                      # 매일(휴일 포함) 09/12/15/18/21시 + 로그온
    .\scripts\road_collector.ps1 install -Slots 10:00,16:00   # 시각을 직접 정한다
    .\scripts\road_collector.ps1 status     # 스케줄 + 쌓인 현황
    .\scripts\road_collector.ps1 now        # 지금 한 번 즉시 수집 (모자란 회차만)
    .\scripts\road_collector.ps1 now -Full  # 그날 것을 전부 다시 받는다
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

    # 깨울 시각들. PC가 켜져 있을 만한 때를 넓게 훑는다 — 앞선 시각을 놓쳐도
    # 다음 시각이 받고, 이미 받았으면 호출 없이 끝나므로 늘려도 비용이 없다.
    [string[]]$Slots = @('09:00', '12:00', '15:00', '18:00', '21:00'),

    # now에서만 쓴다. 모자란 회차만이 아니라 그날 것을 전부 다시 받는다.
    [switch]$Full
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
    param([long]$Code)
    switch ($Code) {
        0          { '성공 (받았거나, 이미 받아서 건너뜀)' }
        1          { '수집 실패 (한도 초과 등 — now로 손수 돌려 확인)' }
        267011     { '아직 실행 전' }
        267009     { '실행 중' }
        267014     { '중지됨' }
        # 0x40010004. PC를 끄거나 로그오프하면 실행 중이던 작업이 이렇게 끊긴다.
        # 2026-09-03 08:29에 실제로 이 코드로 끝나 그날치가 통째로 비었다 —
        # 여러 시각을 걸게 된 계기다.
        1073807364 { '중도 종료 (0x40010004 — 종료·로그오프 등으로 끊김)' }
        default    { "코드 $Code" }
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

    # `--if-needed`가 이 스케줄의 전제다. 하루에 여러 번 깨우되, 그날 몫을 이미
    # 채웠으면 TMAP을 **한 번도 부르지 않고** 끝난다. 잘린 회차만 있으면 그것만
    # 채운다. 그래서 아래 시각을 늘려도 호출은 하루 20건을 넘지 않는다.
    $action = New-ScheduledTaskAction -Execute $PythonW `
        -Argument ('"{0}" --if-needed' -f $Script) -WorkingDirectory $Root

    $triggers = @()

    # ① 켜져 있을 만한 시간대를 훑는다. 앞선 시각에 PC가 꺼져 있었으면 다음
    #    시각이 받고, 이미 받았으면 그냥 끝난다. 새벽을 뺀 이유는 이 프로젝트를
    #    쓰는 환경이 새벽에 PC를 켜 두지 않기 때문이다(2026-09-03).
    #    요일은 전부 건다 — `--if-needed`가 그날의 실제 요일로 평일/휴일을
    #    스스로 갈라 수집하므로(1.26.158), 주말만 따로 막을 이유가 없다.
    foreach ($slot in $Slots) {
        $when = [datetime]::ParseExact($slot.Trim(), 'HH:mm', $null)
        $triggers += New-ScheduledTaskTrigger -Weekly -At $when `
            -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday
    }

    # ② 켜자마자도 한 번. 슬롯을 전부 놓친 날(늦게 켠 날)을 위한 안전망이다.
    $logon = New-ScheduledTaskTrigger -AtLogOn
    $logon.Delay = 'PT3M'          # 부팅 직후 혼잡을 피한다
    $triggers += $logon

    # StartWhenAvailable을 **켠다** — 재고 수집기와 반대다. 저쪽은 10분 뒤 다음
    # 틱이 오지만 이쪽은 하루치가 통째로 걸려 있다.
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    # -ErrorAction Stop을 명시한다. Register-ScheduledTask가 던지는 CIM
    # 예외(예: 관리자 권한 없이 기존 작업을 덮어쓸 때의 Access denied)는
    # 스크립트 전역의 $ErrorActionPreference='Stop'을 그냥 지나쳐 화면에
    # 오류 텍스트만 찍고 다음 줄로 넘어간다 — 그래서 등록이 실패해도 아래
    # "[등록] 성공" 안내가 그대로 떴다(2026-09-08 실제로 겪음). 여기서
    # 명시적으로 멈추게 해야 그 뒤 안내 줄이 거짓말을 하지 않는다.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Settings $settings -Force -ErrorAction Stop `
        -Description "TMAP 고정 패널 실도로 소요시간을 매일(휴일 포함) 수집합니다 - 그날 요일로 평일/휴일 계수를 자동으로 갈라 잽니다. 시각 $($Slots -join ', ') + 로그온 시. 그날 몫을 이미 받았으면 호출하지 않습니다 (docs/구현/COLLECTOR_ROAD.md)." | Out-Null

    Write-Host "[등록] $TaskName — 매일(휴일 포함) $($Slots -join ', ') + 로그온 시" -ForegroundColor Green
    Write-Host "  그날 처음 깨는 실행만 TMAP 20호출을 쓰고, 나머지는 즉시 끝납니다."
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
    if ($Full) {
        Write-Host "[즉시 수집] 그날 것을 전부 다시 받습니다 — TMAP 20호출." -ForegroundColor Cyan
        & $Python $Script
    } else {
        Write-Host "[즉시 수집] 모자란 회차만 채웁니다 (다 찼으면 호출 없이 끝납니다)." -ForegroundColor Cyan
        & $Python $Script --if-needed
    }
}


switch ($Command) {
    'install'   { Invoke-Install }
    'pause'     { Invoke-Pause }
    'resume'    { Invoke-Resume }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
    'now'       { Invoke-Now }
}
