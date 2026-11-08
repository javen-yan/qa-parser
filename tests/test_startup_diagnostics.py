import json
import sys
from pathlib import Path

from qa_parser import startup_diagnostics as diagnostic


def test_source_mark_alone_is_not_claimed_as_root_cause():
    report = {'files': [{'zone_id': 3}], 'assembly_probe': {'status': 'loaded'}}
    assert '原因尚未确认' in diagnostic.explanation(report)
    report['assembly_probe'] = {'errors': [{'hresult': '0x80131515'}]}
    assert '复现' in diagnostic.explanation(report)
    report['files'] = []
    assert '原因尚未确认' in diagnostic.explanation(report)


def test_standalone_command_has_no_policy_changes_or_raw_exception_output():
    command = diagnostic.customer_command()
    for forbidden in ('Unblock-File', 'Remove-Item', 'ExecutionPolicy', 'Exception.Message', 'HostUrl', 'ReferrerUrl'):
        assert forbidden not in command
    assert 'Get-FileHash' in command and 'ZoneId' in command
    assert max(map(len, command.splitlines())) < 8000


def test_diagnostic_entry_precedes_webview_and_clr(tmp_path, monkeypatch):
    from qa_parser import desktop_window
    called = []
    monkeypatch.setattr(diagnostic, 'run', lambda root, path: called.append(path))
    monkeypatch.setitem(sys.modules, 'webview', None)
    monkeypatch.setitem(sys.modules, 'clr', None)
    target = tmp_path / 'diagnostic.json'
    assert desktop_window.main(['--diagnose-startup', str(target)]) == 0
    assert called == [target]


def test_no_raw_powershell_output_in_failure(monkeypatch,tmp_path):
    import subprocess
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('SystemRoot', 'C:/Windows')
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(subprocess, 'run', lambda *a,**kw: subprocess.CompletedProcess(a,1,stderr=b'PRIVATE_PATH_OR_URL'))
    try: diagnostic.run(tmp_path,tmp_path/'report.json')
    except RuntimeError as exc: assert 'PRIVATE' not in str(exc)
    else: raise AssertionError('Diagnostic must fail visibly')
