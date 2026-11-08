import copy
import importlib
import json
from pathlib import Path
import pytest

@pytest.fixture
def setup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    c=importlib.import_module('cleanup_oss_draft')
    repo='javen-yan/qa-parser'; commit='a'*40; tag='candidate-aaaaaaaaaaaa-123'
    owner=dict(schema_version=1,repository=repo,commit=commit,run_id='123',mode='manual',tag=tag)
    release=dict(draft=True,body='<!-- qa-parser-release-owner:'+json.dumps(owner)+' -->',tag_name=tag,target_commitish=commit)
    name='qa-parser-windows-x64-candidate.zip'; key=f'qa-parser/candidates/{tag}/windows-x64/{name}'
    item=dict(key=key,url='https://maiya-reader.oss-cn-beijing.aliyuncs.com/'+key,sha256='b'*64,bytes=12)
    receipt=dict(status='uploaded-and-public-sha256-verified',repository=repo,release_id='9',draft=True,commit=commit,targets=[dict(uploads=[item])])
    current={name:dict(id=1,state='uploaded',digest='sha256:'+'b'*64,size=12)}
    meta_name='release-metadata-windows-x64.json'
    meta_item=dict(item,key=key.rsplit('/',1)[0]+'/'+meta_name,url=item['url'].rsplit('/',1)[0]+'/'+meta_name)
    receipt['targets'][0]['uploads'].append(meta_item)
    current[meta_name]=dict(current[name],id=2)
    run=dict(status='completed',head_sha=commit,path='.github/workflows/release.yml')
    calls=[]
    def api(endpoint,method='GET'):
        calls.append((endpoint,method))
        if method=='DELETE':return None
        return run if '/actions/runs/' in endpoint else release
    monkeypatch.setattr(c,'api',api);monkeypatch.setattr(c,'assets',lambda *a:copy.deepcopy(current))
    monkeypatch.setattr(c,'verify_public',lambda *a:None)
    return c,repo,release,receipt,current,run,calls

def test_only_exact_verified_draft_deleted(setup):
    c,repo,release,receipt,current,run,calls=setup
    assert c.cleanup(repo,'9',receipt)['status']=='verified-ready-to-delete'
    assert not any(method=='DELETE' for _,method in calls)
    assert c.cleanup(repo,'9',receipt,delete=True)['status']=='deleted'
    assert [p for p,m in calls if m=='DELETE']==[f'repos/{repo}/releases/9']

@pytest.mark.parametrize('change',['running','published','extra','digest','foreign_url','incomplete'])
def test_cleanup_guards_keep_draft(setup,change):
    c,repo,release,receipt,current,run,calls=setup
    if change=='running':run['status']='in_progress'
    elif change=='published':release['draft']=False
    elif change=='extra':current['unknown.txt']=dict(size=1)
    elif change=='digest':next(iter(current.values()))['digest']='sha256:'+'c'*64
    elif change=='foreign_url':receipt['targets'][0]['uploads'][0]['url']='https://example.com/secret'
    elif change=='incomplete':receipt['status']='uploaded-and-public-head-verified'
    with pytest.raises(ValueError):c.cleanup(repo,'9',receipt,delete=True)
    assert not any(method=='DELETE' for _,method in calls)

def test_public_sha_failure_preserves_draft(setup,monkeypatch):
    c,repo,release,receipt,current,run,calls=setup
    def failed(*a):raise ValueError('download SHA differs')
    monkeypatch.setattr(c,'verify_public',failed)
    with pytest.raises(ValueError):c.cleanup(repo,'9',receipt,delete=True)
    assert not any(m=='DELETE' for _,m in calls)

def test_deleted_candidate_is_idempotent(setup,monkeypatch):
    c,repo,release,receipt,current,run,calls=setup
    def absent(*a):raise RuntimeError('GitHub GET failed: HTTP 404')
    monkeypatch.setattr(c,'api',absent)
    assert c.cleanup(repo,'9',receipt,delete=True)['status']=='already-absent'
