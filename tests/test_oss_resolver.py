import importlib
import json
from pathlib import Path
import pytest


def test_resolver_binds_trusted_completed_run_and_skips_archived_candidate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    r=importlib.import_module('resolve_oss_release')
    repo='javen-yan/qa-parser';commit='a'*40
    run=dict(status='completed',conclusion='success',event='workflow_dispatch',head_branch='main',head_sha=commit,head_repository={'full_name':repo},path='.github/workflows/release.yml')
    owner=dict(schema_version=1,repository=repo,commit=commit,run_id='12',mode='manual',tag='candidate-aaaaaaaaaaaa-12')
    release=dict(id=9,draft=True,body=r.PREFIX+json.dumps(owner)+' -->',tag_name=owner['tag'],target_commitish=commit)
    monkeypatch.setattr(r,'api',lambda endpoint:run if '/actions/runs/' in endpoint else [release])
    assert r.resolve(repo,12,commit)=={'release_id':'9','skip':'false'}
    release.update(draft=False,prerelease=True)
    release['body']+=f'<!-- qa-parser-oss-delivery:9:{commit} -->'
    assert r.resolve(repo,12,commit)['skip']=='true'
    for key,value in [('status','in_progress'),('event','pull_request'),('head_branch','untrusted'),('head_repository',{'full_name':'other/repo'}),('head_sha','b'*40)]:
        old=run[key];run[key]=value
        with pytest.raises(ValueError,match='trusted'):r.resolve(repo,12,commit)
        run[key]=old


def test_resolver_accepts_exact_formal_tag_run(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    r=importlib.import_module('resolve_oss_release');repo='javen-yan/qa-parser';commit='a'*40
    run=dict(status='completed',conclusion='success',event='push',head_branch='v0.3.0',head_sha=commit,head_repository={'full_name':repo},path='.github/workflows/release.yml')
    owner=dict(schema_version=1,repository=repo,commit=commit,run_id='12',mode='tag',tag='v0.3.0')
    release=dict(id=9,draft=False,prerelease=False,body=r.PREFIX+json.dumps(owner)+' -->',tag_name=owner['tag'],target_commitish=commit)
    monkeypatch.setattr(r,'api',lambda endpoint:run if '/actions/runs/' in endpoint else [release])
    assert r.resolve(repo,12,commit)=={'release_id':'9','skip':'false'}
    run['head_branch']='v0.4.0'
    with pytest.raises(ValueError,match='exact version'):r.resolve(repo,12,commit)
