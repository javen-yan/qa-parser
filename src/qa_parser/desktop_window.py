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


def service_stopper(process):
    """Serialize the closed callback and finalizer; never re-signal a reaped PGID."""
    lock = threading.Lock()
    stopped = False

    def stop():
        nonlocal stopped
        with lock:
            if not stopped:
                stop_service(process)
                stopped = True
    return stop


def _main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--diagnose-startup']:
        from .startup_diagnostics import run
        root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()
        destination = Path(args[1]) if len(args) == 2 else user_data_directory() / 'diagnostics/startup-diagnostic.json'
        run(root, destination)
        return 0
    if args[:1] == ['--cli'] or os.environ.get('QA_PARSER_CLI_LOG'):
        _stdio_for_windowed_cli()
    if args[:1] == ['--cli']:
        if os.environ.get('QA_PARSER_STARTUP_DIAGNOSTICS'):
            import faulthandler
            faulthandler.dump_traceback_later(20, file=sys.stderr)
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
                               env=dict(os.environ, QA_PARSER_CLI_LOG=str(data / 'desktop.log'),
                                        **({'QA_PARSER_STARTUP_DIAGNOSTICS': '1'} if smoke else {})),
                               **({'creationflags': subprocess.CREATE_NO_WINDOW}
                                  if sys.platform == 'win32' else {}))
    stop = service_stopper(process)
    instance = data / 'server.json'
    try:
        deadline = time.monotonic() + 30
        while not instance.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                if smoke:
                    print(f'Service startup failed: exit={process.poll()}, pid={process.pid}', flush=True)
                    with (data / 'desktop.log').open('rb') as diagnostic:
                        diagnostic.seek(0, os.SEEK_END)
                        diagnostic.seek(max(0, diagnostic.tell() - 8192))
                        print(diagnostic.read().decode('utf-8', errors='replace'), flush=True)
                    if sys.platform == 'darwin' and process.poll() is None:
                        sampled = subprocess.run(['/usr/bin/sample', str(process.pid), '1', '1'],
                                                 capture_output=True, text=True, timeout=10, check=False)
                        print(sampled.stdout.split('Binary Images:')[0][:6000], flush=True)
                        print((data / 'desktop.log').read_text(encoding='utf-8', errors='replace')[-3000:], flush=True)
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
            stop()

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
        stop()
        instance.unlink(missing_ok=True)
        log.close()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(args)
    except Exception as error:
        # CLI errors retain their existing contracts. Only an interactive
        # Windows startup gets a bounded, private-data-free diagnostic report.
        if sys.platform != 'win32' or args:
            raise
        from .startup_diagnostics import run, explanation
        destination = user_data_directory() / 'diagnostics/startup-diagnostic.json'
        try:
            root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()
            report = run(root, destination)
            report['startup_exception_type'] = type(error).__name__
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            message = explanation(report) + '\n\n诊断报告：' + str(destination)
        except Exception:
            message = 'Windows 窗口初始化失败，诊断未能完成。请使用独立启动诊断工具；不要修改系统安全设置。'
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, '题库转换：启动诊断', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
