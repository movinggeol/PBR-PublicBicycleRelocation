<#
.SYNOPSIS
    Python 3.14를 사용자 PATH 맨 앞에 넣는다 (한 번만 하면 되는 셋업).

.DESCRIPTION
    `py` 런처 없이 `python`을 쳤을 때 3.14가 잡히게 한다. 저장소 규약은
    `.venv\Scripts\python.exe`를 쓰는 것이므로 파이프라인에는 필요 없고,
    새 환경에서 `.venv`를 **만들 때** 필요하다.

    ⚠️ 1.26.143까지 설치 경로가 `C:\Users\이동걸\AppData\...`로 **박혀 있었다.**
    이 저장소는 두 환경(회사·집)에서 돌므로, 사용자 이름이 다른 PC에서는 없는
    경로를 PATH에 넣게 된다 — 조용히 아무 일도 안 하는 상태다. 지금은
    `$env:LOCALAPPDATA`로 풀고, **실제로 있는지 확인한 뒤에만** 넣는다.

.EXAMPLE
    .\scripts\set_user_path_314.ps1
    .\scripts\set_user_path_314.ps1 -PythonHome "D:\Python\pythoncore-3.14-64"
#>
[CmdletBinding()]
param(
    # 설치 경로를 직접 줄 수 있다. 생략하면 이 PC의 기본 자리를 쓴다.
    [string]$PythonHome = (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64')
)

$ErrorActionPreference = 'Stop'

$marker = 'pythoncore-3.14'

$user = [Environment]::GetEnvironmentVariable('PATH', 'User')
if (-not $user) { $user = '' }

Write-Host '--- 현재 사용자 PATH ---'
Write-Host $user

if ($user -like "*$marker*") {
    Write-Host "[건너뜀] PATH에 이미 $marker 가 있습니다." -ForegroundColor DarkGray
    return
}

# 없는 경로를 넣지 않는다. 예전에는 확인 없이 넣어서, 그 PC에 3.14가 없으면
# PATH만 길어지고 `python`은 여전히 옛 버전이 잡혔다.
if (-not (Test-Path $PythonHome)) {
    Write-Warning "설치 경로를 찾을 수 없습니다: $PythonHome"
    Write-Warning "  3.14를 먼저 설치하거나 -PythonHome 으로 실제 경로를 주세요."
    return
}

$new = "$PythonHome;$user"

# ⚠️ `[Environment]::SetEnvironmentVariable`을 쓰지 않는다 — 그쪽은 값을
# REG_SZ로 쓰기 때문에, PATH에 이미 들어 있던 `%USERPROFILE%` 같은 항목이
# 확장된 채 굳어 버린다. 레지스트리에 ExpandString으로 직접 쓴다.
$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
if ($null -eq $key) {
    [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment') | Out-Null
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
}
$key.SetValue('PATH', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString)
$key.Close()
Write-Host "[반영] $PythonHome 을 PATH 맨 앞에 넣었습니다." -ForegroundColor Green

# 새로 뜨는 프로세스가 바뀐 값을 읽도록 WM_SETTINGCHANGE를 방송한다.
# (이미 열려 있는 창에는 적용되지 않는다 — 새 창을 열어야 한다.)
$signature = @"
using System;
using System.Runtime.InteropServices;
public class NativeMethods {
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
        uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
}
"@
Add-Type -TypeDefinition $signature | Out-Null
$result = [UIntPtr]::Zero
# HWND_BROADCAST(0xffff) · WM_SETTINGCHANGE(0x1A) · SMTO_ABORTIFHUNG(0x2)
[NativeMethods]::SendMessageTimeout(
    [IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 0x2, 5000, [ref]$result) | Out-Null

Write-Host '--- 반영된 사용자 PATH ---'
$check = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
Write-Host $check.GetValue('PATH', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
Write-Host '이미 열려 있는 창에는 적용되지 않습니다 — 새 창을 여세요.'
