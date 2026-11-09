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
    assert 'ComputeHash' in command and 'ZoneId' in command
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


def trusted_report():
    return {'files': [dict(relative_path=name, sha256=sha, zone_id=3,
                          authenticode_status='Valid' if '/webview/' in name else 'NotSigned')
                      for name, sha in diagnostic.RELEASE_HASHES.items()],
            'assembly_probe': {'status': 'failed', 'errors': [{'hresult': '0x80131515'}]}}


def test_recovery_requires_complete_trusted_evidence():
    from copy import deepcopy
    good = trusted_report()
    assert diagnostic.recovery_plan(good) == list(diagnostic.RELEASE_HASHES)
    for field, value in [('sha256', 'tampered'), ('zone_id', 4),
                         ('authenticode_status', 'HashMismatch'), ('authenticode_status', 'NotTrusted')]:
        bad = deepcopy(good); bad['files'][0][field] = value
        assert diagnostic.recovery_plan(bad) == []
    bad = deepcopy(good); bad['files'].pop()
    assert diagnostic.recovery_plan(bad) == []
    bad = deepcopy(good); bad['files'][-1] = bad['files'][0]
    assert diagnostic.recovery_plan(bad) == []
    bad = deepcopy(good); bad['assembly_probe'] = {'status':'loaded'}
    assert diagnostic.recovery_plan(bad) == []
    bad = deepcopy(good); bad['files'][2]['authenticode_status'] = 'NotSigned'
    assert diagnostic.recovery_plan(bad) == []


def test_recovery_only_removes_allowlisted_stream_and_keeps_failure_journal(tmp_path, monkeypatch):
    import hashlib
    names = ['_internal/one.dll', '_internal/two.dll']
    hashes = {name:hashlib.sha256(name.encode()).hexdigest() for name in names}
    for name in [*names, 'unrelated.dll']:
        path = tmp_path/name;path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        Path(str(path)+':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n')
    monkeypatch.setattr(diagnostic, 'RELEASE_HASHES', hashes)
    monkeypatch.setattr(sys, 'platform', 'win32')
    before = {'files':[dict(relative_path=n,sha256=h,zone_id=3,authenticode_status='NotSigned') for n,h in hashes.items()],
              'assembly_probe':{'errors':[{'hresult':'0x80131515'}]}}
    monkeypatch.setattr(diagnostic, 'run', lambda *a:before)
    original = Path.unlink
    def guarded(path, *args, **kwargs):
        if str(path).endswith('two.dll:Zone.Identifier'):raise PermissionError('PRIVATE_PATH')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',guarded)
    output = tmp_path/'diagnostics/recovery.json';output.parent.mkdir()
    diagnostic.auto_recover(tmp_path,output)
    report = json.loads(output.read_text())
    assert report['changed'] == [names[0]] and report['error_type'] == 'PermissionError'
    assert 'PRIVATE_PATH' not in output.read_text()
    assert Path(str(tmp_path/'unrelated.dll')+':Zone.Identifier').is_file()
    assert all((tmp_path/n).read_bytes()==n.encode() for n in names)
