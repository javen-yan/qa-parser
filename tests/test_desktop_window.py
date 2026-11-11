"""Desktop lifecycle contracts without launching a GUI or using a browser."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import threading

import pytest

from qa_parser.desktop_window import service_command, stop_service


def test_closed_callback_and_finalizer_stop_service_once(monkeypatch):
    from qa_parser import desktop_window
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def cleanup(process):
        calls.append(process)
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(desktop_window, 'stop_service', cleanup)
    process = object()
    stop = desktop_window.service_stopper(process)
    callback = threading.Thread(target=stop)
    finalizer = threading.Thread(target=stop)
    callback.start()
    assert entered.wait(3)
    finalizer.start()
    release.set()
    callback.join(3)
    finalizer.join(3)
    stop()
    assert not callback.is_alive() and not finalizer.is_alive()
    assert calls == [process]


def test_service_command_preserves_unicode_paths_and_uses_no_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    folder = tmp_path / '中文 data'
    assert service_command(folder) == [sys.executable, '-m', 'qa_parser', 'ui',
                                      '--no-browser', '--data-dir', str(folder)]
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/Applications/题库 转换.app/Contents/MacOS/题库转换')
    assert service_command(folder) == [sys.executable, '--cli', 'ui',
                                      '--no-browser', '--data-dir', str(folder)]


def _running(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True,
                            text=True, check=False)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


def _wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.025)
    assert predicate()


@pytest.mark.skipif(os.name != 'posix', reason='Native POSIX process-group contract')
@pytest.mark.parametrize('leader_exits_first', [False, True])
def test_stop_service_reaps_owned_renderer_even_after_leader_exits(tmp_path, leader_exits_first):
    # The renderer ignores TERM, so killing only the service or relying only on
    # a graceful termination cannot satisfy this test. The unrelated sibling
    # remains alive, guarding against an overly broad process-name kill.
    ready = tmp_path / 'renderer.json'
    child_code = ('import os, signal, time, pathlib, json; '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  f'pathlib.Path({str(ready)!r}).write_text(json.dumps({{"pid":os.getpid()}})); '
                  'time.sleep(60)')
    leader_code = ('import subprocess, sys, time; '
                   f'subprocess.Popen([sys.executable, "-c", {child_code!r}]); '
                   'time.sleep(60)')
    leader = subprocess.Popen([sys.executable, '-c', leader_code], start_new_session=True)
    sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                               start_new_session=True)
    child_pid = None
    try:
        _wait_until(lambda: ready.exists() and bool(ready.read_text()))
        child_pid = json.loads(ready.read_text())['pid']
        assert os.getpgid(child_pid) == leader.pid
        if leader_exits_first:
            leader.terminate()
            leader.wait(timeout=3)
            assert _running(child_pid)
        stop_service(leader)
        assert leader.poll() is not None
        _wait_until(lambda: not _running(child_pid))
        assert sibling.poll() is None
        # Finalizer and closed event may both invoke cleanup.
        stop_service(leader)
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        leader.wait(timeout=3)
        sibling.terminate()
        sibling.wait(timeout=3)


def test_macos_never_uses_windows_split_layout(monkeypatch):
    import sys
    from qa_parser.windows_distribution import split_app
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert split_app('/Applications/app/v1.0.0/题库转换') is None

