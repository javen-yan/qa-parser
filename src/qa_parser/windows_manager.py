"""Frozen Windows-only manager, independent of the business interpreter and WebView."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .windows_distribution import APP_EXE, atomic_json, file_lock
from .windows_update import installed, read_state, recover, stage_update, rollback, finish_start


def message(text, flags=0):
    return ctypes.windll.user32.MessageBoxW(None, str(text), '题库转换 · 离线更新', flags)


def choose_update():
    class OPENFILENAME(ctypes.Structure):
        _fields_ = [('lStructSize', W.DWORD), ('hwndOwner', W.HWND), ('hInstance', W.HINSTANCE),
                    ('lpstrFilter', W.LPCWSTR), ('lpstrCustomFilter', W.LPWSTR), ('nMaxCustFilter', W.DWORD),
                    ('nFilterIndex', W.DWORD), ('lpstrFile', W.LPWSTR), ('nMaxFile', W.DWORD),
                    ('lpstrFileTitle', W.LPWSTR), ('nMaxFileTitle', W.DWORD), ('lpstrInitialDir', W.LPCWSTR),
                    ('lpstrTitle', W.LPCWSTR), ('Flags', W.DWORD), ('nFileOffset', W.WORD),
                    ('nFileExtension', W.WORD), ('lpstrDefExt', W.LPCWSTR), ('lCustData', ctypes.c_ssize_t),
                    ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', W.LPCWSTR), ('pvReserved', ctypes.c_void_p),
                    ('dwReserved', W.DWORD), ('FlagsEx', W.DWORD)]
    buffer = ctypes.create_unicode_buffer(32768)
    dialog = OPENFILENAME()
    dialog.lStructSize = ctypes.sizeof(dialog)
    dialog.lpstrFilter = '离线更新包 (*.qaupdate)\0*.qaupdate\0\0'
    dialog.lpstrFile = ctypes.cast(buffer, W.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrTitle = '选择题库转换离线更新包'
    dialog.Flags = 0x1000 | 0x800 | 0x8  # file/path exist, no cwd change
    return Path(buffer.value) if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)) else None


class ChildJob:
    """Kill owned business/service/render children if manager exits unexpectedly."""
    def __init__(self):
        class BASIC(ctypes.Structure):
            _fields_ = [('a', ctypes.c_int64), ('b', ctypes.c_int64), ('flags', W.DWORD),
                        ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', W.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]
        class EXTENDED(ctypes.Structure):
            _fields_ = [('basic', BASIC), ('io', IO), ('process', ctypes.c_size_t),
                        ('job', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = W.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
            process.kill()
            process.wait()
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def launch(root, public_key, args=(), probe=False):
    state = read_state(root)
    version = state['current']
    app, data = installed(root, version, public_key)
    nonce = secrets.token_hex(24)
    gate = root / ('.launch-' + nonce + '.json')
    ready = root / ('.ready-' + nonce + '.json')
    job = ChildJob()
    process = None
    try:
        # Child waits before acquiring resources or spawning services, avoiding assignment races.
        process = subprocess.Popen([str(app / APP_EXE), '--managed-launch', nonce, *args], cwd=app,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        job.attach(process)
        atomic_json(gate, {'nonce': nonce})
        if args:  # Native smoke reports cover both local service and page readiness.
            code = process.wait(timeout=120 if probe else None)
            if probe:
                expected = {'nonce': nonce, 'version': version, 'ready': True}
                healthy = code == 0 and ready.exists() and json.loads(ready.read_text()) == expected
                finish_start(root, version, healthy)
                return 0 if healthy else 1
            return code
        deadline = time.monotonic() + 60
        success = False
        while process.poll() is None and time.monotonic() < deadline:
            if ready.exists():
                response = json.loads(ready.read_text(encoding='utf-8'))
                success = response == {'nonce': nonce, 'version': version, 'ready': True}
                if success:
                    break
            time.sleep(.1)
        finish_start(root, version, success)
        if not success:
            job.close()
            process.wait(timeout=10)
            message('新版本未能完成启动，已保留或恢复上一可用版本。请重新打开程序。', 0x10)
            return 1
        # A conversion failure after startup is not an update failure.
        return process.wait()
    finally:
        job.close()
        if process is not None and process.poll() is None:
            process.wait(timeout=10)
        gate.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)


def main(argv=None):
    if sys.platform != 'win32':
        raise RuntimeError('Windows manager only')
    root = Path(sys.executable).resolve().parent
    from qa_update_key import PUBLIC_KEY_HEX  # generated at build time, not loaded from installation data
    public_key = bytes.fromhex(PUBLIC_KEY_HEX)
    args = list(sys.argv[1:] if argv is None else argv)
    test_report = Path(args[2]) if len(args) == 3 and args[0] == '--test-update' else None
    try:
        with file_lock(root, 'operation'):
            with file_lock(root, 'activity'):
                recover(root)
                if args == ['--manage']:
                    state = read_state(root)
                    choice = message(f"当前程序：{state['current']}\n\n是：安装离线更新\n否：恢复上一版本\n取消：关闭", 3)
                    if choice == 6:
                        archive = choose_update()
                        if archive is None:
                            return 0
                        stage_update(root, archive, public_key)
                    elif choice == 7:
                        rollback(root, public_key)
                    else:
                        return 0
                    args = []
                elif test_report:
                    stage_update(root, Path(args[1]), public_key)
                    args = ['--desktop-smoke', str(test_report)]
                elif args[:1] == ['--install-update'] and len(args) == 2:
                    stage_update(root, Path(args[1]), public_key)
                    args = []
                elif args == ['--rollback']:
                    rollback(root, public_key)
                    args = []
                elif args and args[0] not in ('--cli', '--desktop-smoke', '--runtime-smoke', '--diagnose-startup'):
                    raise ValueError('Unknown manager arguments')
            # Hold shared activity for the whole owned process tree, including between child startup/exit.
            with file_lock(root, 'activity', shared=True):
                try:
                    return launch(root, public_key, args, probe=bool(test_report))
                except Exception:
                    state = read_state(root)
                    if state.get('pending'):
                        finish_start(root, state['current'], False)
                    raise
    except Exception as error:
        if test_report:
            atomic_json(test_report, {'status': 'failed', 'error': type(error).__name__})
        else:
            message('安装目录不可写，请将完整程序文件夹移到当前用户可写的位置后重试。'
                    if isinstance(error, PermissionError) else str(error), 0x10)
        return 1
