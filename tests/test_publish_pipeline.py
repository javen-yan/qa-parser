import importlib
import json
from pathlib import Path
from urllib.error import HTTPError
import pytest


@pytest.mark.parametrize('upload_fails', [False, True])
def test_draft_remains_private_until_public_upload_verified(monkeypatch, tmp_path, upload_fails):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    p = importlib.import_module('publish_pipeline')
    calls = []
    monkeypatch.setattr(p, 'settings', lambda env: {})
    monkeypatch.setattr(p, 'check_idle', lambda: None)
    monkeypatch.setattr(p.release_draft, 'api', lambda endpoint: {'draft': True})
    owner = dict(commit='a' * 40, run_id='1', mode='tag', tag='v1.0.0')
    monkeypatch.setattr(p, 'release_owner', lambda *a: (owner, 'qa-parser/releases/v1.0.0'))
    monkeypatch.setattr(p, 'verify', lambda *a, **kw: {'original_run_conclusion': 'failure'})
    monkeypatch.setattr(p.release_draft, 'check_tag_commit', lambda *a: None)
    monkeypatch.setattr(p, 'source_assembler', lambda *a: lambda: None)
    def no_receipt(*a, **kw): raise HTTPError('url', 404, '', None, None)
    monkeypatch.setattr(p, 'urlopen', no_receipt)
    def finalize(*a, **kw):
        assert kw['keep_draft'] is True and callable(kw['assembler'])
        calls.append('assemble-private')
    monkeypatch.setattr(p.release_draft, 'finalize', finalize)
    def sync(argv):
        calls.append('public-sha-verification')
        if upload_fails: raise ValueError('upload incomplete')
        work = Path(argv[argv.index('--work') + 1]);work.mkdir()
        (work / 'delivery-record.json').write_text('{}')
    monkeypatch.setattr(p, 'sync', sync)
    def cleanup(*a, **kw):
        assert kw == {'apply': True, 'verify_native_jobs': True}
        calls.append('publish-and-clean')
        return {'release_url': 'url'}
    monkeypatch.setattr(p, 'cleanup', cleanup)
    monkeypatch.setattr(p.sys, 'argv', ['publish_pipeline', '--release-id', '123', '--work', str(tmp_path/'work'), '--evidence', str(tmp_path/'evidence')])
    if upload_fails:
        with pytest.raises(ValueError): p.main()
        assert calls == ['assemble-private', 'public-sha-verification']
    else:
        p.main()
        assert calls == ['assemble-private', 'public-sha-verification', 'publish-and-clean']
