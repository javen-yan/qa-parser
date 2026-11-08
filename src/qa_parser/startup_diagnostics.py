"""Read-only Windows startup probe, usable before importing clr or webview.

No filenames outside the fixed runtime set, environment values, exception
messages, download URLs, usernames or document data enter the JSON report.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

RELEASE_HASHES = {
    '_internal/clr_loader/ffi/dlls/amd64/ClrLoader.dll': '07f62bcb1b70320f221ffb2cfc5ce6133815195f3e7cdb8d833222506188c2ac',
    '_internal/pythonnet/runtime/Python.Runtime.dll': '2ebd4492e28442ef1f1af587afe5b3a090c2ebfa220758ded4f1450f2a27f13a',
    '_internal/webview/lib/Microsoft.Web.WebView2.Core.dll': '8219a3135436c147f8eb02fb831e99596103eebc18b6d7b794ca93f4c67018a2',
    '_internal/webview/lib/Microsoft.Web.WebView2.WinForms.dll': 'c48f8d729dc531288d2c53a6241effe7922ed1b947850b2442008a945c098c48',
}

POWERSHELL = r'''
$ErrorActionPreference='Stop';
$root=$env:QA_DIAG_APP_DIR; $dest=$env:QA_DIAG_REPORT;
function Failures($e) {
  $items=@(); for($i=0;$e -and $i -lt 8;$i++) {
    $items+=@{type=$e.GetType().FullName;hresult=('0x{0:X8}' -f $e.HResult)};
    $e=$e.InnerException;
  }; return ,$items;
};
$result=@{schema_version=1;probe='windows-startup/1';read_only=$true;file_reference_version='v0.3.1';
  process_64bit=[Environment]::Is64BitProcess;os_64bit=[Environment]::Is64BitOperatingSystem;
  clr_version=[Environment]::Version.ToString();files=@();
  pythonnet_runtime_override_present=([bool]$env:PYTHONNET_RUNTIME)};
try {
  $os=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion';
  $result.os=@{build=$os.CurrentBuildNumber;ubr=$os.UBR;display_version=$os.DisplayVersion};
} catch {$result.os_error=Failures $_.Exception};
try {
  $net=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full';
  $result.netfx_release=$net.Release;
} catch {$result.netfx_error=Failures $_.Exception};
try {$result.drive_type=([IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))).DriveType.ToString()}
catch {$result.drive_error=Failures $_.Exception};
$expected=ConvertFrom-Json '__HASHES__';
foreach($property in $expected.PSObject.Properties) {
  $relative=$property.Name; $path=Join-Path $root $relative;
  $row=@{relative_path=$relative;exists=(Test-Path -LiteralPath $path -PathType Leaf);zone_id=$null};
  if($row.exists) {
    try {$row.sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower();
      $row.matches_v031=($row.sha256 -eq $property.Value)} catch {$row.hash_error=Failures $_.Exception};
    try {
      $zone=Get-Content -LiteralPath $path -Stream Zone.Identifier -ErrorAction Stop;
      foreach($line in $zone) {if($line -match '^ZoneId=(\d+)$') {$row.zone_id=[int]$Matches[1]}};
    } catch {$row.zone_read='absent_or_unavailable'};
  }; $result.files+=,$row;
};
$runtime=Join-Path $root '_internal/pythonnet/runtime/Python.Runtime.dll';
try {
  $checked=@($result.files|Where-Object {$_.relative_path -eq '_internal/pythonnet/runtime/Python.Runtime.dll'});
  if(-not $checked[0].matches_v031) {throw [Security.SecurityException]::new('Unverified runtime file')};
  $assembly=[Reflection.Assembly]::LoadFrom($runtime);
  $loader=$assembly.GetType('Python.Runtime.Loader',$true);
  $method=$loader.GetMethod('Initialize',[Reflection.BindingFlags]'Public,Static');
  $result.assembly_probe=@{status='loaded';initialize_found=($null -ne $method);
    assembly_version=$assembly.GetName().Version.ToString();image_runtime=$assembly.ImageRuntimeVersion};
} catch {$result.assembly_probe=@{status='failed';errors=(Failures $_.Exception)}};
$directory=Split-Path -Parent $dest; [IO.Directory]::CreateDirectory($directory)|Out-Null;
$result|ConvertTo-Json -Depth 9|Set-Content -LiteralPath $dest -Encoding UTF8;
'''.replace('__HASHES__', json.dumps(RELEASE_HASHES, separators=(',', ':')))


def run(root: Path, output: Path) -> dict:
    if sys.platform != 'win32':
        raise RuntimeError('Windows-only startup diagnostic')
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', POWERSHELL],
                            env=dict(os.environ, QA_DIAG_APP_DIR=str(root.resolve()), QA_DIAG_REPORT=str(output.resolve())),
                            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    # Never persist raw PowerShell output: it may contain customer paths.
    if result.returncode:
        raise RuntimeError(f'Diagnostic helper exited with code {result.returncode}')
    return json.loads(output.read_text(encoding='utf-8-sig'))


def customer_command() -> str:
    # A direct, inspectable command; no execution-policy change or unblocking.
    code = ' '.join(line.strip() for line in POWERSHELL.splitlines() if line.strip())
    # JSON's double quotes need PowerShell literals instead for cmd.exe quoting.
    encoded = json.dumps(RELEASE_HASHES, separators=(',', ':'))
    # Pass the JSON via a process-local environment variable instead of shell code.
    code = code.replace("ConvertFrom-Json '" + encoded + "'", 'ConvertFrom-Json $env:QA_DIAG_HASHES')
    assert '"' not in code and '%' not in code
    command = ('@echo off\r\nsetlocal\r\n'
               'set "QA_DIAG_APP_DIR=%~dp0"\r\n'
               'set "QA_DIAG_REPORT=%LOCALAPPDATA%\\QA Parser\\diagnostics\\startup-diagnostic.json"\r\n'
               'set QA_DIAG_HASHES=' + encoded + '\r\n'
               '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive -Command "' + code + '"\r\n'
               'if errorlevel 1 (echo Diagnostic failed. No application files changed.) else (echo Report: "%QA_DIAG_REPORT%")\r\n'
               'pause\r\n')
    assert max(map(len, command.splitlines())) < 8000
    return command


def explanation(report: dict) -> str:
    errors = report.get('assembly_probe', {}).get('errors', [])
    blocked = any(e.get('hresult') == '0x80131515' for e in errors)
    marked = any(f.get('zone_id') in (3, 4) for f in report.get('files', []))
    if blocked and marked:
        return ('已检测到 Internet 来源标记，并复现 .NET 程序集加载被阻止。'
                '请先核对下载 ZIP 的 SHA-256；确认来自正式下载地址后，在 ZIP 属性中解除锁定，'
                '再完整解压到一个新文件夹。程序不会自动解除任何文件的安全标记。')
    return ('Windows 窗口运行时初始化失败，原因尚未确认。'
            '请提供诊断报告以核对程序集、.NET Framework 和来源标记；无需先安装 Python 或修改文件夹名称。')
