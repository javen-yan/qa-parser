"""Windows startup probe and pinned-component recovery before clr or webview.

No filenames outside the fixed runtime set, environment values, exception
messages, download URLs, usernames or document data enter the JSON report.
"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import re

RELEASE_HASHES = {
    '_internal/clr_loader/ffi/dlls/amd64/ClrLoader.dll': '07f62bcb1b70320f221ffb2cfc5ce6133815195f3e7cdb8d833222506188c2ac',
    '_internal/pythonnet/runtime/Python.Runtime.dll': '2ebd4492e28442ef1f1af587afe5b3a090c2ebfa220758ded4f1450f2a27f13a',
    '_internal/webview/lib/Microsoft.Web.WebView2.Core.dll': '8219a3135436c147f8eb02fb831e99596103eebc18b6d7b794ca93f4c67018a2',
    '_internal/webview/lib/Microsoft.Web.WebView2.WinForms.dll': 'c48f8d729dc531288d2c53a6241effe7922ed1b947850b2442008a945c098c48',
}

POWERSHELL = r'''
$ErrorActionPreference='Stop';
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1');
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
  powershell_version=$PSVersionTable.PSVersion.ToString();
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
    try {
      $stream=[IO.File]::OpenRead($path); $hasher=[Security.Cryptography.SHA256]::Create();
      try {$row.sha256=([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-','').ToLower()}
      finally {$stream.Dispose();$hasher.Dispose()};
      $row.matches_v031=($row.sha256 -eq $property.Value);
      $row.authenticode_status=(Get-AuthenticodeSignature -LiteralPath $path).Status.ToString();
    } catch {$row.hash_error=Failures $_.Exception};
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
    env = {k:v for k,v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    env.update(QA_DIAG_APP_DIR=str(root.resolve()), QA_DIAG_REPORT=str(output.resolve()))
    result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', POWERSHELL],
                            env=env,
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
               'set "PSModulePath="\r\n'
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
                '本次未能完成受限自动恢复，诊断已保存。请将诊断报告交给技术支持；'
                '无需安装 Python、修改文件夹名称或关闭安全软件。')
    return ('Windows 窗口运行时初始化失败，原因尚未确认。'
            '请提供诊断报告以核对程序集、.NET Framework 和来源标记；无需先安装 Python 或修改文件夹名称。')


def zone_id(path: Path):
    try:
        with Path(str(path) + ':Zone.Identifier').open('r', encoding='utf-8-sig') as stream:
            text = stream.read(4096)
        match = re.search(r'^ZoneId=(\d+)\s*$', text, re.M)
        return int(match[1]) if match else None
    except FileNotFoundError:
        return None


def recovery_plan(report: dict) -> list[str]:
    """Require positive loading-error, whole allowlist hash, zone and signature evidence."""
    rows = report.get('files', [])
    if len(rows) != len(RELEASE_HASHES) or {r.get('relative_path') for r in rows} != set(RELEASE_HASHES):
        return []
    if not any(e.get('hresult') == '0x80131515' for e in report.get('assembly_probe', {}).get('errors', [])):
        return []
    if any(r.get('sha256') != RELEASE_HASHES[r['relative_path']] or r.get('zone_id') not in (None, 0, 1, 2, 3) for r in rows):
        return []
    marked = [r for r in rows if r.get('zone_id') == 3]
    for row in rows:
        allowed = {'Valid'} if '/webview/' in row['relative_path'] else {'Valid', 'NotSigned'}
        if row.get('authenticode_status') not in allowed:
            return []
    return [r['relative_path'] for r in marked]


def auto_recover(root: Path, output: Path):
    """Recover this pinned distribution's Internet-marked managed components only.

    No policy/registry changes, recursive unblocking, remote-load switch, or
    recovery for changed binaries, restricted zones, signatures or permissions.
    """
    if sys.platform != 'win32':
        return
    root = root.absolute()
    record = {'status': 'automatic_recovery_failed', 'changed': []}
    try:
        if not any(zone_id(root / name) in (3, 4) for name in RELEASE_HASHES):
            return
        before = run(root, output)
        plan = recovery_plan(before)
        record = {'status': 'not_automatically_recoverable', 'changed': [], 'before': before}
        for name in plan:
            path = root / name
            # Refuse redirects outside or within this private package boundary.
            if any(p.is_symlink() or getattr(p.stat(), 'st_file_attributes', 0) & 0x400
                   for p in [path, *list(path.parents)[:len(Path(name).parts)]]):
                raise PermissionError('Package contains a reparse point')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != RELEASE_HASHES[name]:
                    raise ValueError('Component changed after diagnostic')
            if zone_id(path) != 3:
                raise ValueError('Provenance changed after diagnostic')
            Path(str(path) + ':Zone.Identifier').unlink()
            record['changed'].append(name)
        if plan:
            after = run(root, output)
            record['after'] = after
            if after.get('assembly_probe', {}).get('status') == 'loaded':
                record['status'] = 'recovered_verified_internet_mark'
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as error:
        output.parent.mkdir(parents=True, exist_ok=True)
        record.update(status='automatic_recovery_failed', error_type=type(error).__name__)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        # Do not escalate permissions or change security policy. Normal startup
        # may still work; if not, the native error UI reports this limitation.
