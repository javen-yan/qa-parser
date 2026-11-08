"""Native window hosting the existing UI; service lifetime belongs to window."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .desktop import user_data_directory


def configure_windows_runtime(webview) -> str | None:
    """Pin the renderer to our portable runtime, never silently use Edge/IE."""
    if sys.platform != 'win32':
        return None
    root = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
            else Path(os.environ.get('QA_PARSER_WINDOWS_RESOURCES', 'build/windows/resources')).resolve())
    runtime = root / 'webview2'
    if not (runtime / 'msedgewebview2.exe').is_file():
        raise RuntimeError('离线浏览器组件缺失，请重新完整解压程序包。')
    webview.settings['WEBVIEW2_RUNTIME_PATH'] = str(runtime)
    os.environ['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'] = str(runtime)
    return str(runtime)


def _stdio_for_windowed_cli() -> None:
    # PyInstaller --windowed sets these to None on Windows, even when the
    # parent redirects handles. CLI evidence is persisted in its output files.
    diagnostic = os.environ.get('QA_PARSER_CLI_LOG')
    if diagnostic:
        destination = Path(diagnostic)
        destination.parent.mkdir(parents=True, exist_ok=True)
        stream = destination.open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = stream
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))


def service_command(data: Path) -> list[str]:
    if getattr(sys, 'frozen', False):
        base = [sys.executable, '--cli']
    else:
        base = [sys.executable, '-m', 'qa_parser']
    return base + ['ui', '--no-browser', '--data-dir', str(data)]


def stop_service(process: subprocess.Popen) -> None:
    """Stop the service and renderer children, including during an active job."""
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # A renderer may outlive the leader; reap the whole owned session.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, timeout=10, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    process.wait(timeout=5)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--cli'] or os.environ.get('QA_PARSER_CLI_LOG'):
        _stdio_for_windowed_cli()
    if args[:1] == ['--cli']:
        try:
            from .cli import main as cli_main
            return cli_main(args[1:])
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
    if len(args) == 3 and args[0] == '--runtime-smoke':
        from .formula_images import preview_to_png
        result = preview_to_png(Path(args[1]).read_bytes(), '.wmf')
        png = result.pop('png', None)
        report = Path(args[2]).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        if png:
            report.with_suffix('.png').write_bytes(png)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if result['status'] == 'converted' else 1
    smoke = Path(args[1]).resolve() if len(args) == 2 and args[0] == '--desktop-smoke' else None
    import webview

    runtime = configure_windows_runtime(webview)

    data = user_data_directory() / 'desktop' / uuid.uuid4().hex
    data.mkdir(parents=True)
    log = (data / 'desktop.log').open('a', encoding='utf-8')
    # Never inherit a development cwd or depend on resources next to the app.
    process = subprocess.Popen(service_command(data), cwd=data, stdout=log, stderr=log,
                               start_new_session=os.name == 'posix',
                               **({'creationflags': subprocess.CREATE_NO_WINDOW,
                                   'env': dict(os.environ, QA_PARSER_CLI_LOG=str(data / 'desktop.log'))}
                                  if sys.platform == 'win32' else {}))
    instance = data / 'server.json'
    try:
        deadline = time.monotonic() + 30
        while not instance.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f'转换服务未能启动，诊断文件：{data / "desktop.log"}')
            time.sleep(.1)
        origin = json.loads(instance.read_text(encoding='utf-8'))['url']
        if sys.platform == "darwin":
            from AppKit import NSScreen
            available = NSScreen.mainScreen().visibleFrame().size
            max_width, max_height = int(available.width) - 40, int(available.height) - 40
        else:
            screen = webview.screens[0]
            max_width, max_height = screen.width - 60, screen.height - 100
        window = webview.create_window('题库转换', origin,
                                       width=min(1120, max_width), height=min(800, max_height),
                                       min_size=(min(760, max_width), min(600, max_height)), text_select=True)
        closed = threading.Event()

        def on_closed(*_):
            closed.set()
            stop_service(process)

        def watch_service():
            process.wait()
            if not closed.is_set():
                window.destroy()

        window.events.closed += on_closed
        smoke_passed = threading.Event()
        if smoke:
            def smoke_loaded():
                result = {'status': 'failed', 'runtime': runtime, 'data_directory': str(data)}
                try:
                    # Exercise the production page, pinned math adapter and
                    # actual font download through the local native WebView.
                    # The pinned Cocoa and EdgeChromium backends return a
                    # JSON string from run_js without evaluate_js's eval wrapper.
                    window.run_js("""document.fonts.load('16px "QA Formula Unicode"').then(fonts => { window.__qaFontReady = fonts.length > 0 && document.fonts.check('16px "QA Formula Unicode"'); }); void 0;""")
                    end = time.monotonic() + 20
                    while time.monotonic() < end:
                        if window.run_js('JSON.stringify(window.__qaFontReady === true)') == 'true':
                            break
                        time.sleep(.1)
                    checks = json.loads(window.run_js(r'''JSON.stringify({
                      title: document.title,
                      controls: !!document.querySelector('#add') && !!document.querySelector('#file-input'),
                      font: window.__qaFontReady === true,
                      formula: QaMathPreview.assess('\\frac{U_{总}^{2}}{R}').supported,
                      engine: QaMathPreview.version,
                      userAgent: navigator.userAgent
                    })'''))
                    result['checks'] = checks
                    if not all(checks.get(k) for k in ('controls', 'font', 'formula')):
                        raise RuntimeError('Native page, font or formula check failed')
                    result['status'] = 'passed'
                    smoke_passed.set()
                except Exception as error:
                    result['error'] = str(error)
                finally:
                    smoke.parent.mkdir(parents=True, exist_ok=True)
                    smoke.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    window.destroy()
            window.events.loaded += smoke_loaded
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.start(watch_service, private_mode=True, debug=False,
                      **({'gui': 'edgechromium', 'storage_path': str(data / 'webview-cache')}
                         if sys.platform == 'win32' else {}))
        return 1 if smoke and not smoke_passed.is_set() else 0
    finally:
        stop_service(process)
        instance.unlink(missing_ok=True)
        log.close()


if __name__ == '__main__':
    raise SystemExit(main())
