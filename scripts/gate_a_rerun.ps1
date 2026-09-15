<#
.SYNOPSIS
    게이트 A 채택 3단계 — 이동시간에 기대는 실험을 정본 스냅샷으로 한꺼번에 다시 돌린다
    (docs/기록/수집완료_계획.md 2-5 · docs/기록/TODO.md 게이트 A 채택 작업).

.DESCRIPTION
    EXPERIMENTS 각 장의 '재현' 명령을 그대로 옮기되 **재고 스냅샷만 정본
    `2026-08-11 real`로 박는다.** 스크립트 대부분의 `--run-label` 기본값은 정본이
    아니라 *최신 계획*이라, 인자를 빼면 같은 명령이 날마다 다른 재고로 돈다
    (DECISIONS 6-B · EXPERIMENTS 31장).

    같은 목록을 **두 번** 돌린다 — 스위치를 끈 채(`-Mode off`) 한 번, 켠 채(`-Mode on`)
    한 번. 장마다 표가 만들어진 날의 코드·스냅샷이 제각각이라, 지금 문서의 수치와
    켠 결과를 나란히 놓으면 이동시간 때문인지 재고·코드가 달라서인지 가를 수 없다.

    시작 전에 멈추는 조건:
      · 스위치 상태가 -Mode와 다르다 (on인데 .env 세 줄이 안 읽힌다 — 1.26.208 전의 함정)
      · 정본 스냅샷이 이 PC의 DB에 없다
    경고만 하는 조건:
      · 오늘 도로 수집(400구간)이 아직 안 끝났다 — 실험이 메모리를 조이면 수집 틱이 같이 실패할 수 있다
      · 여유 메모리가 3GB 미만 — 격자 실험이 CBC를 못 띄워 WinError 8로 죽는다(2-5)

    결과는 `data\gate_a_rerun\<mode>_<시각>\` 에 작업마다 .csv(있으면)·.log·.err와
    `_요약.csv`로 남는다. 폴더를 시각으로 나누므로 이전 결과를 덮지 않는다.

.EXAMPLE
    .\scripts\gate_a_rerun.ps1 -Mode off -DryRun      # 명령과 점검 결과만 찍는다
    .\scripts\gate_a_rerun.ps1 -Mode off              # 스위치를 끈 채 기준값
    .\scripts\gate_a_rerun.ps1 -Mode on               # .env 세 줄을 넣은 뒤
    .\scripts\gate_a_rerun.ps1 -Mode on -Only gamma_sweep,convention_sweep   # 죽은 것만 다시

.NOTES
    TMAP을 부르지 않는다 — 실험은 식으로 계산한다. 데이터를 지우지 않는다.
    7장(차량 대수)은 재현이 실험 스크립트가 아니라 파이프라인 반복이라 목록에 없다.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('off', 'on')]
    [string]$Mode,

    [switch]$DryRun,

    # 작업 이름(아래 목록의 Name)으로 일부만 돌린다.
    [string[]]$Only = @()
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Label = '2026-08-11 real'
$Expect = @{ Fixed = 320.4; Speed = 32.11 }   # road_time_model.py 2026-09-15 판정값

if (-not (Test-Path $Py)) { throw "가상환경이 없습니다: $Py" }
Set-Location $Root

$O = 'data\gate_a_rerun\{0}_{1}' -f $Mode, (Get-Date -Format 'yyyyMMdd_HHmm')   # 인자에 넣는 상대 경로
$OutDir = Join-Path $Root $O

# --- 작업 목록: 장 · 스크립트 · 인자 (EXPERIMENTS '재현' 줄에서 라벨만 정본으로) ---
$Jobs = @(
    @{ Name = 'budget_enforce_120'; Ch = '5-E'; Script = 'baseline\budget_enforce.py'
       Args = @('--run-label', $Label, '--budget', '120') }
    @{ Name = 'budget_enforce_91'; Ch = '5-E'; Script = 'baseline\budget_enforce.py'
       Args = @('--run-label', $Label, '--budget', '91') }
    @{ Name = 'baseline_compare'; Ch = '5-F·30'; Script = 'baseline\baseline_compare.py'
       Args = @('--period', '25년 11월', '--day-type', 'weekday', '--seed', '42', '--run-label', $Label, '--out', "$O\baseline_compare.csv") }
    @{ Name = 'cluster_count_sweep'; Ch = '5-G'; Script = 'params\cluster_count_sweep.py'
       Args = @(); Env = $true }
    @{ Name = 'wanted_vehicles_geo_sweep'; Ch = '5-H'; Script = 'params\wanted_vehicles_geo_sweep.py'
       Args = @(); Env = $true }
    @{ Name = 'ortools_gap_2511'; Ch = '6'; Script = 'baseline\ortools_gap.py'
       Args = @('--period', '25년 11월', '--limit-sec', '10', '--run-label', $Label, '--out', "$O\ortools_gap_2511.csv") }
    @{ Name = 'multi_cluster_route'; Ch = '8·27'; Script = 'structure\multi_cluster_route.py'
       Args = @('--period', '25년 11월', '--chain-size', '2', '--run-label', $Label, '--out', "$O\multi_cluster_route.csv") }
    @{ Name = 'z_stockout_grid'; Ch = '11'; Script = 'params\z_stockout_grid.py'
       Args = @('--run-label', $Label, '--period', '25년 11월', '--out', "$O\z_stockout_grid.csv") }
    @{ Name = 'limit_fleet_grid'; Ch = '12'; Script = 'params\limit_fleet_grid.py'
       Args = @('--run-label', $Label, '--period', '25년 11월', '--out', "$O\limit_fleet_grid.csv") }
    @{ Name = 'tour_length_estimate'; Ch = '13'; Script = 'diagnostic\tour_length_estimate.py'
       Args = @('--run-label', $Label, '--out', "$O\tour_length_estimate.csv") }
    @{ Name = 'repeat_eval_12m'; Ch = '15·33'; Script = 'baseline\repeat_eval.py'
       Args = @('--run-label', $Label, '--periods', '25년 01월,25년 04월,25년 05월,25년 06월,25년 07월,25년 08월,25년 09월,25년 10월,25년 11월,26년 01월,26년 02월,26년 03월',
                '--seeds', '42,7,13', '--methods', 'P,B0,B1', '--day-type', 'weekday', '--out', "$O\repeat_eval_12m.csv") }
    @{ Name = 'gamma_sweep'; Ch = '5-C·16'; Script = 'params\gamma_sweep.py'
       Args = @('--run-label', $Label, '--gammas', '1000,3000,5000,8000', '--periods', '25년 09월,25년 11월,26년 01월,26년 03월',
                '--seeds', '42,7,13', '--out', "$O\gamma_sweep.csv") }
    @{ Name = 'limit_fixedpop_grid'; Ch = '17'; Script = 'params\limit_fixedpop_grid.py'
       Args = @('--period', '25년 11월', '--run-label', $Label, '--limits', '15,20,25,30,40,50', '--out', "$O\limit_fixedpop_grid.csv") }
    @{ Name = 'z_fixedpop_grid'; Ch = '18'; Script = 'params\z_fixedpop_grid.py'
       Args = @('--run-label', $Label, '--out', "$O\z_fixedpop_grid.csv") }
    @{ Name = 'z_fixedpop_wide'; Ch = '18·22'; Script = 'params\z_fixedpop_grid.py'
       Args = @('--run-label', $Label, '--z-grid', 'wide', '--seeds', '42,7,13,21,99', '--out', "$O\z_fixedpop_wide.csv") }
    @{ Name = 'convention_sweep'; Ch = '19'; Script = 'params\convention_sweep.py'
       Args = @('--run-label', $Label, '--out', "$O\convention_sweep.csv") }
    @{ Name = 'cluster_time_term'; Ch = '21'; Script = 'params\cluster_time_term.py'
       Args = @('--run-label', $Label, '--out', "$O\cluster_time_term.csv") }
    @{ Name = 'ortools_gap_2603'; Ch = '23'; Script = 'baseline\ortools_gap.py'
       Args = @('--period', '26년 03월', '--run-label', $Label, '--limit-sec', '60', '--out', "$O\ortools_gap_2603.csv") }
    @{ Name = 'ortools_gap_seeds'; Ch = '24'; Script = 'baseline\ortools_gap_seeds.py'
       Args = @('--run-label', $Label, '--seeds', '42,7,13', '--limit-sec', '60', '--out-dir', "$O\ortools_gap_seeds") }
    @{ Name = 'fleet_outage_stress'; Ch = '25'; Script = 'structure\fleet_outage_stress.py'
       Args = @('--outages', '0,1,3,5,7,9', '--seeds', '42,7,13,21,99') }
    @{ Name = 'budget_split'; Ch = '26'; Script = 'structure\budget_split.py'
       Args = @('--run-label', $Label, '--road-factor', '1.0') }
    @{ Name = 'center_stat_grid'; Ch = '28'; Script = 'params\center_stat_grid.py'
       Args = @('--run-label', $Label, '--z-grid', '1.65,1.99,2.33', '--seeds', '42,7,13', '--out', "$O\center_stat_grid.csv") }
    @{ Name = 'fulfill_gross'; Ch = '29'; Script = 'structure\fulfill_gross.py'
       Args = @('--run-label', $Label, '--out', "$O\fulfill_gross.csv") }
    @{ Name = 'gamma_recheck'; Ch = '4 후속-2'; Script = 'baseline\gamma_recheck.py'
       Args = @('--period', '26년 03월', '--run-label', $Label, '--out', "$O\gamma_recheck.csv") }
)

if ($Only.Count -gt 0) {
    $unknown = $Only | Where-Object { $n = $_; -not ($Jobs | Where-Object { $_.Name -eq $n }) }
    if ($unknown) { throw "목록에 없는 작업: $($unknown -join ', ')" }
    $Jobs = $Jobs | Where-Object { $Only -contains $_.Name }
}

function Quote-Arg([string]$a) {
    if ($a -match '[\s,]') { return '"' + $a + '"' }
    return $a
}

# --- 점검 ---
$problems = @()

$state = & $Py -c "import project_config as p; print(p.USE_ROAD_MODEL, p.ROAD_FIXED_SEC_WEEKDAY, p.ROAD_SPEED_KMPH_WEEKDAY, round(p.travel_seconds(1.0)))"
$use, $fixed, $speed, $sec1 = ($state -split '\s+')
Write-Host ("[스위치] USE_ROAD_MODEL={0} · 고정비 {1}초 · 속도 {2} km/h · 1km {3}초" -f $use, $fixed, $speed, $sec1)
if ($Mode -eq 'on') {
    if ($use -ne 'True' -or [double]$fixed -ne $Expect.Fixed -or [double]$speed -ne $Expect.Speed) {
        $problems += "-Mode on인데 스위치가 판정값으로 켜져 있지 않습니다 (.env에 PBR_USE_ROAD_MODEL=1 · PBR_ROAD_FIXED_SEC_WEEKDAY=320.4 · PBR_ROAD_SPEED_KMPH_WEEKDAY=32.11)"
    }
} elseif ($use -ne 'False') {
    $problems += "-Mode off인데 스위치가 켜져 있습니다. .env의 PBR_USE_ROAD_MODEL을 지우고 다시 실행하십시오"
}

$today = Get-Date -Format 'yyyy-MM-dd'
# 컨텍스트 관리자 객체를 변수에 붙들어 둔다 — 임시 객체로 두면 바로 수거되어 연결이 닫힌다
$check = & $Py -c "import db; cm=db.session(); c=cm.__enter__(); print(c.execute('SELECT COUNT(*) FROM station_info WHERE run_label=?', ['$Label']).fetchone()[0], c.execute('SELECT COUNT(*) FROM road_leg WHERE run_label=?', ['roadprobe-$today']).fetchone()[0])"
if ($LASTEXITCODE -ne 0) { throw "DB 점검이 실패했습니다 (위 오류 참고)" }
$stations, $legs = ($check -split '\s+')
Write-Host ("[스냅샷] '{0}' 대여소 {1}곳" -f $Label, $stations)
if ([int]$stations -eq 0) { $problems += "정본 스냅샷 '$Label'이 이 PC의 DB에 없습니다 (두_PC_작업.md 4-3)" }

Write-Host ("[도로 수집] 오늘(roadprobe-{0}) {1}구간" -f $today, $legs)
if ([int]$legs -lt 400) {
    Write-Warning "오늘 도로 수집이 아직 400구간이 아닙니다. 실험이 메모리를 조이면 수집이 같이 실패할 수 있으니 .\scripts\road_collector.ps1 now 로 먼저 끝내십시오."
}

$freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
Write-Host "[메모리] 여유 $freeGB GB"
if ($freeGB -lt 3) { Write-Warning "여유 메모리가 3GB 미만입니다. 브라우저·편집기를 닫고 돌리십시오 (WinError 8)." }

Write-Host "[작업] $($Jobs.Count)개 → $O"
foreach ($j in $Jobs) {
    $argLine = (@("experiments\$($j.Script)") + ($j.Args | ForEach-Object { Quote-Arg $_ })) -join ' '
    $envNote = if ($j.Env) { "  (PBR_RUN_LABEL=$Label)" } else { '' }
    Write-Host ("  {0,-8} {1,-26} python {2}{3}" -f $j.Ch, $j.Name, $argLine, $envNote)
}

if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host "[멈춤] $_" -ForegroundColor Red }
    if (-not $DryRun) { exit 1 }
}
if ($DryRun) { Write-Host '[DryRun] 실행하지 않았습니다.'; exit 0 }

# --- 실행 ---
New-Item -ItemType Directory -Force $OutDir | Out-Null
# ortools_gap_seeds만 --out-dir을 받는다 — 그 작업을 돌릴 때만 만든다
if ($Jobs | Where-Object { $_.Name -eq 'ortools_gap_seeds' }) {
    New-Item -ItemType Directory -Force (Join-Path $OutDir 'ortools_gap_seeds') | Out-Null
}
$env:PBR_RUN_LABEL = $Label      # 인자 대신 환경변수로 스냅샷을 받는 두 스크립트용
$summary = Join-Path $OutDir '_요약.csv'
'name,chapter,exit,minutes' | Out-File -Encoding utf8 $summary

$i = 0
foreach ($j in $Jobs) {
    $i++
    $argLine = (@("experiments\$($j.Script)") + ($j.Args | ForEach-Object { Quote-Arg $_ })) -join ' '
    $t0 = Get-Date
    Write-Host ("[{0}/{1}] {2} ({3}장) 시작 {4:HH:mm}" -f $i, $Jobs.Count, $j.Name, $j.Ch, $t0)
    $p = Start-Process -FilePath $Py -ArgumentList $argLine -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $OutDir "$($j.Name).log") `
        -RedirectStandardError (Join-Path $OutDir "$($j.Name).err")
    $min = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    $mark = if ($p.ExitCode -eq 0) { 'OK  ' } else { 'FAIL' }
    Write-Host ("        {0} exit {1} · {2}분" -f $mark, $p.ExitCode, $min)
    "$($j.Name),$($j.Ch),$($p.ExitCode),$min" | Out-File -Encoding utf8 -Append $summary
}

Write-Host "[끝] $summary"
