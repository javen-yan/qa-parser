from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from qa_parser.desktop_window import configure_windows_runtime
from qa_parser.desktop import open_local_path


def test_fixed_webview_runtime_required_no_system_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    webview = SimpleNamespace(settings={})
    with pytest.raises(RuntimeError, match='离线浏览器'):
        configure_windows_runtime(webview)
    folder = tmp_path / 'webview2'
    folder.mkdir()
    (folder / 'msedgewebview2.exe').write_bytes(b'fixture')
    assert configure_windows_runtime(webview) == str(folder)
    assert webview.settings['WEBVIEW2_RUNTIME_PATH'] == str(folder)


def test_macos_runtime_untouched(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    webview = SimpleNamespace(settings={})
    assert configure_windows_runtime(webview) is None
    assert webview.settings == {}


def test_windows_open_result_uses_shell_path_without_command(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(sys, 'platform', 'win32')
    seen = []
    monkeypatch.setattr(os, 'startfile', seen.append, raising=False)
    folder = tmp_path / '中文 result'
    folder.mkdir()
    open_local_path(folder)
    assert seen == [str(folder.resolve())]
