[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Requires native Windows x64' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$BuildRoot = Join-Path $ProjectRoot 'build/windows'
$Resources = Join-Path $BuildRoot 'resources'
$DistRoot = Join-Path $ProjectRoot 'dist/windows'
$AppName = '题库转换'
if (Test-Path $Resources) { throw 'Resource directory already exists; use a clean build.' }
if (Test-Path $DistRoot) { throw 'Distribution directory already exists; use a clean build.' }
& python -c 'import platform,struct; assert platform.system()=="Windows" and struct.calcsize("P")==8 and platform.machine().lower() in ("amd64","x86_64")'
if ($LASTEXITCODE -ne 0) { throw 'Expected Windows x64 Python' }
& python scripts/windows_dependencies.py
if ($LASTEXITCODE -ne 0) { throw 'Pinned runtime staging failed' }
New-Item -ItemType Directory -Force $BuildRoot | Out-Null
$Entry = Join-Path $BuildRoot 'desktop_entry.py'
@'
from qa_parser.desktop_window import main
if __name__ == '__main__':
    raise SystemExit(main())
'@ | Set-Content -Encoding UTF8 $Entry
$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot 'cache'
$Arguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
    '--name', $AppName, '--icon', (Join-Path $ProjectRoot 'assets/icon.ico'),
    '--distpath', $DistRoot, '--workpath', (Join-Path $BuildRoot 'freeze'), '--specpath', $BuildRoot,
    '--paths', (Join-Path $ProjectRoot 'src'), '--collect-data', 'qa_parser',
    '--collect-all', 'webview', '--collect-all', 'pythonnet', '--collect-all', 'clr_loader',
    '--hidden-import', 'webview.platforms.edgechromium', '--hidden-import', 'webview.platforms.winforms',
    '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6', '--exclude-module', 'PySide6',
    '--exclude-module', 'webview.platforms.cocoa', '--exclude-module', 'webview.platforms.qt',
    '--exclude-module', 'webview.platforms.gtk', '--exclude-module', 'pytest', $Entry)
& python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$App = Join-Path $DistRoot $AppName
$Internal = Join-Path $App '_internal'
foreach ($Name in @('webview2','wmf','licenses')) {
    Copy-Item -Recurse (Join-Path $Resources $Name) (Join-Path $Internal $Name)
}
Copy-Item (Join-Path $Resources 'resources-manifest.json') $Internal
Copy-Item (Join-Path $Resources 'resources-manifest.json') $BuildRoot
$Manifest = @{
    schema_version = 1
    platform = 'win32'
    architecture = 'x64'
    source_commit = $env:GITHUB_SHA
    runner_os = [Environment]::OSVersion.VersionString
    python = (& python --version)
    status = 'built; frozen execution checks pending'
    real_reference = 'not uploaded; not tested'
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $BuildRoot 'build-manifest.json')
Copy-Item (Join-Path $PSScriptRoot 'WINDOWS-THIRD-PARTY-NOTICES.txt') $App
@'
题库转换 — Windows x64 云端验证候选
请完整解压后双击“题库转换.exe”，不要只复制EXE。
运行依赖随包携带，无需安装Python、Office、MathType、Node或字体。
任务和日志位于当前用户LOCALAPPDATA，不写入程序目录。
本包尚不代表真实题库、客户电脑或目标系统验收。
可选LibreOffice原图引擎未包含，处理不完整时请查看遗漏清单。
第三方通知与分发限制请阅读WINDOWS-THIRD-PARTY-NOTICES.txt。
'@ | Set-Content -Encoding UTF8 (Join-Path $App '使用说明.txt')
Write-Output "Built $App; execution validation is required before packaging."
