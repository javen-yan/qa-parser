"""Frozen macOS update manager, independent of the business application."""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .macos_distribution import (APP_NAME, APP_EXECUTABLE, atomic_json, file_lock,
                                 read_state, root_for_manager)
from .macos_update import finish_start, recover, rollback, stage_update

TITLE = '题库转换 · 离线更新'
READY_TIMEOUT = 60


def public_key():
    try:
        from qa_update_key import PUBLIC_KEY_HEX  # generated at build time
    except ImportError:
        pinned = Path(__file__).resolve().parents[2] / 'packaging/update-public-key.txt'
        PUBLIC_KEY_HEX = pinned.read_text(encoding='utf-8').strip()
    return bytes.fromhex(PUBLIC_KEY_HEX)


def log(root, text):
    try:
        with (Path(root) / 'update.log').open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + ' ' + text + '\n')
    except OSError:
        pass


def message(text, *, critical=False):
    from AppKit import (NSAlert, NSInformationalAlertStyle, NSCriticalAlertStyle,
                        NSApplication)
    NSApplication.sharedApplication()
    alert = NSAlert.alloc().init()
    alert.setMessageText_(TITLE)
    alert.setInformativeText_(str(text))
    alert.setAlertStyle_(NSCriticalAlertStyle if critical else NSInformationalAlertStyle)
    alert.addButtonWithTitle_('好')
    alert.runModal()


def choose_action():
    from AppKit import NSAlert, NSApplication
    NSApplication.sharedApplication()
    alert = NSAlert.alloc().init()
    alert.setMessageText_(TITLE)
    alert.setInformativeText_('选择要执行的操作。更新前请关闭所有题库转换窗口并等待转换结束。')
    alert.addButtonWithTitle_('安装离线更新')
    alert.addButtonWithTitle_('恢复上一版本')
    alert.addButtonWithTitle_('取消')
    response = alert.runModal()
    return {1000: 'install', 1001: 'rollback'}.get(response)


def choose_update():
    from AppKit import NSOpenPanel, NSApplication
    NSApplication.sharedApplication()
    panel = NSOpenPanel.openPanel()
    panel.setTitle_('选择题库转换离线更新包')
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setAllowedFileTypes_(['zip', 'qaupdate'])
    if panel.runModal() != 1 or not panel.URLs():
        return None
    return Path(panel.URLs()[0].path())


def _executable(root):
    return Path(root) / APP_NAME / APP_EXECUTABLE


def _wait_ready(root, process, nonce, version):
    ready = Path(root) / ('.ready-' + nonce + '.json')
    deadline = time.monotonic() + READY_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if ready.exists():
                try:
                    response = json.loads(ready.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    response = {}
                if response == {'nonce': nonce, 'version': version, 'ready': True}:
                    return True
            if process.poll() is not None:
                return False
            time.sleep(.1)
        return False
    finally:
        ready.unlink(missing_ok=True)


def _activate(root, version, *, test_report=None):
    """Launch the current application and confirm readiness (or a smoke run)."""
    executable = _executable(root)
    if not executable.is_file():
        return False
    if test_report is not None:
        report = Path(test_report)
        report.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([str(executable), '--desktop-smoke', str(report)],
                                   cwd=root, start_new_session=True)
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            return False
        try:
            return code == 0 and json.loads(report.read_text(encoding='utf-8')).get('status') == 'passed'
        except (OSError, ValueError):
            return False
    nonce = secrets.token_hex(24)
    process = subprocess.Popen([str(executable), '--update-ready', nonce],
                               cwd=root, start_new_session=True)
    healthy = _wait_ready(root, process, nonce, version)
    if not healthy:
        try:
            process.terminate()
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            process.kill()
        return False
    return True


def _finish(root, version, healthy):
    if healthy:
        # The application now holds the shared activity lock; the state file is
        # atomic and does not need to exclude its own reader.
        return finish_start(root, version, True)
    with file_lock(root, 'activity'):
        return finish_start(root, version, False)


def install(root, archive, *, test_report=None):
    key = public_key()
    root = Path(root).resolve()
    with file_lock(root, 'operation'):
        with file_lock(root, 'activity'):
            manifest = stage_update(root, archive, key)
        log(root, f'staged {manifest["version"]} from {archive}')
        healthy = _activate(root, manifest['version'], test_report=test_report)
        _finish(root, manifest['version'], healthy)
        log(root, f'activation {manifest["version"]}: {"passed" if healthy else "failed"}')
        if not healthy:
            raise RuntimeError('新版本未能完成启动，已恢复上一可用版本。')
        return manifest


def restore(root, *, test_report=None):
    key = public_key()
    root = Path(root).resolve()
    with file_lock(root, 'operation'):
        with file_lock(root, 'activity'):
            state = rollback(root, key)
        log(root, f'rollback to {state["current"]}')
        healthy = _activate(root, state['current'], test_report=test_report)
        _finish(root, state['current'], healthy)
        if not healthy:
            raise RuntimeError('恢复的版本未能完成启动。')
        return state


def wait_for_activity(root, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        try:
            with file_lock(root, 'activity'):
                return
        except RuntimeError:
            if time.monotonic() > deadline:
                raise
            time.sleep(.2)


def install_request(root, request_path):
    root = Path(root).resolve()
    request = Path(request_path).resolve()
    try:
        try:
            data = json.loads(request.read_text(encoding='utf-8'))
        finally:
            request.unlink(missing_ok=True)
        if (data.get('schema_version') != 1 or data.get('platform') != 'macos' or
                not data.get('archive')):
            raise ValueError('无效的离线更新请求')
        archive = Path(data['archive'])
        if archive.suffix.lower() not in ('.zip', '.qaupdate') or not archive.is_file():
            raise ValueError('请选择 .zip 或 .qaupdate 离线更新包。')
        key = public_key()
        wait_for_activity(root)
        with file_lock(root, 'operation'):
            with file_lock(root, 'activity'):
                manifest = stage_update(root, archive, key)
            healthy = _activate(root, manifest['version'])
            _finish(root, manifest['version'], healthy)
            if not healthy:
                raise RuntimeError('新版本未能完成启动，已恢复上一可用版本。')
            return manifest
    except Exception as error:
        log(root, f'request update failed: {type(error).__name__}: {error}')
        try:
            _activate(root, read_state(root)['current'])
        except Exception:
            pass
        message(str(error), critical=True)
        raise


def _parse(argv):
    args = list(argv)
    root = None
    if '--root' in args:
        index = args.index('--root')
        if index + 1 >= len(args):
            raise ValueError('--root requires a path')
        root = Path(args[index + 1]).resolve()
        del args[index:index + 2]
    if root is None:
        root = root_for_manager()
    return args, root


def main(argv=None):
    args, root = _parse(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ['--test-update'] and len(args) == 3:
            report = Path(args[2]).resolve()
            try:
                manifest = install(root, Path(args[1]), test_report=report)
                atomic_json(report.with_suffix('.manager.json'), {
                    'status': 'passed', 'version': manifest['version'],
                    'state': read_state(root)})
                return 0
            except Exception as error:
                atomic_json(report.with_suffix('.manager.json'), {
                    'status': 'failed', 'error': type(error).__name__,
                    'detail': str(error), 'state': read_state(root)})
                return 1
        if args[:1] == ['--install-request'] and len(args) == 2:
            install_request(root, args[1])
            return 0
        if args[:1] == ['--install-update'] and len(args) == 2:
            install(root, Path(args[1]))
            return 0
        if args[:1] == ['--rollback']:
            test_report = None
            if len(args) == 3 and args[1] == '--test-report':
                test_report = args[2]
            elif len(args) != 1:
                raise ValueError('Unknown rollback arguments')
            restore(root, test_report=test_report)
            return 0
        if args not in ([], ['--manage']):
            raise ValueError('Unknown manager arguments')
        state = recover(root)
        choice = choose_action()
        if choice == 'install':
            archive = choose_update()
            if archive is None:
                return 0
            install(root, archive)
            message(f'已更新到 {read_state(root)["current"]}。')
        elif choice == 'rollback':
            restore(root)
            message(f'已恢复到 {read_state(root)["current"]}。')
        return 0
    except Exception as error:
        log(root, f'error: {type(error).__name__}: {error}')
        if args[:1] == ['--install-request']:
            return 1
        if args[:1] in (['--test-update'], ['--install-update']):
            raise
        message(str(error), critical=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
