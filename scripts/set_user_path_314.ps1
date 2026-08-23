$u = [Environment]::GetEnvironmentVariable('PATH','User')
if(-not $u) { $u = '' }
Write-Output '--- USER PATH BEFORE ---'
Write-Output $u
if($u -notlike '*pythoncore-3.14-64*') {
    $new = 'C:\Users\이동걸\AppData\Local\Python\pythoncore-3.14-64;' + $u
    $reg = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    if($reg -eq $null) { [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment') | Out-Null; $reg = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true) }
    $reg.SetValue('PATH', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    $reg.Close()
    Write-Output '--- REGISTRY UPDATED ---'
    # Broadcast WM_SETTINGCHANGE so new processes pick up the change
    $sig = @"
using System;
using System.Runtime.InteropServices;
public class NativeMethods {
    [DllImport("user32.dll",SetLastError=true,CharSet=CharSet.Auto)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
}
"@
    Add-Type -TypeDefinition $sig | Out-Null
    $outVar = [UIntPtr]::Zero
    [NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]0, 'Environment', 0x2, 5000, [ref]$outVar) | Out-Null
} else {
    Write-Output '--- ALREADY CONTAINS 3.14 ---'
}
Write-Output '--- USER PATH (registry) NOW ---'
$reg2 = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
$reg2.GetValue('PATH')
