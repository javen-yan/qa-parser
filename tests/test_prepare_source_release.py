import importlib
from pathlib import Path
import pytest


@pytest.mark.parametrize('fault', [None, 'branch', 'ref', 'event'])
def test_source_dispatch_validates_context_and_uses_checkout(monkeypatch, fault):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    m = importlib.import_module('prepare_source_release')
    env = dict(GITHUB_REPOSITORY='test/repo', GITHUB_RUN_ID='123', GITHUB_SHA='b'*40,
               GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
               SOURCE_REF='v0.3.4', RELEASE_MODE='tag', RELEASE_TAG='v0.3.4')
    if fault == 'branch': env['GITHUB_REF'] = 'refs/heads/other'
    if fault == 'ref': env['SOURCE_REF'] = 'main'
    if fault == 'event': env['GITHUB_EVENT_NAME'] = 'push'
    for k,v in env.items(): monkeypatch.setenv(k,v)
    monkeypatch.setattr(m.subprocess, 'check_output', lambda *a, **kw: 'a'*40+'\n')
    seen=[]
    monkeypatch.setattr(m.draft, 'check_tag_commit', lambda *a: seen.append(a))
    monkeypatch.setattr(m.draft, 'prepare', lambda *a: seen.append(a))
    monkeypatch.setattr(m.draft, 'ROOT', m.draft.ROOT)
    if fault:
        with pytest.raises(ValueError): m.main()
        assert not seen
    else:
        m.main()
        assert seen[0] == ('test/repo', 'v0.3.4', 'a'*40)
        assert seen[1][1]['commit'] == 'a'*40
        assert seen[1][1]['workflow_commit'] == 'b'*40
