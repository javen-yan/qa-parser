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
    release=dict(draft=True,body='<!-- qa-parser-release-owner:'+json.dumps(owner)+' -->',tag_name=tag,target_commitish=commit,html_url='https://github.com/javen-yan/qa-parser/releases/tag/'+tag)
    name='qa-parser-windows-x64-candidate.zip'; key=f'qa-parser/candidates/{tag}/windows-x64/{name}'
    item=dict(key=key,url='https://maiya-reader.oss-cn-beijing.aliyuncs.com/'+key,sha256='b'*64,bytes=12)
    receipt=dict(status='uploaded-and-public-sha256-verified',repository=repo,release_id='9',draft=True,commit=commit,targets=[dict(target="windows-x64",filename=name,uploads=[item])])
    current={name:dict(id=1,state='uploaded',digest='sha256:'+'b'*64,size=12)}
    meta_name='release-metadata-windows-x64.json'
    meta_item=dict(item,key=key.rsplit('/',1)[0]+'/'+meta_name,url=item['url'].rsplit('/',1)[0]+'/'+meta_name)
    receipt['targets'][0]['uploads'].append(meta_item)
    current[meta_name]=dict(current[name],id=2)
    run=dict(status='completed',head_sha=commit,path='.github/workflows/release.yml')
    calls=[]
    def api(endpoint,method='GET',payload=None):
        calls.append((endpoint,method))
        if method=='DELETE':
            assert '/releases/assets/' in endpoint
            assert '阿里云下载' in release['body']
            asset_id=int(endpoint.rsplit('/',1)[1])
            for name in list(current):
                if current[name]['id']==asset_id:del current[name]
            return None
        if method=='PATCH':release.update(payload)
        return copy.deepcopy(run if '/actions/runs/' in endpoint else release)
    monkeypatch.setattr(c,'api',api);monkeypatch.setattr(c,'assets',lambda *a:copy.deepcopy(current))
    monkeypatch.setattr(c,'verify_public',lambda *a:None)
    return c,repo,release,receipt,current,run,calls

def test_only_verified_attachments_deleted_release_retained(setup):
    c,repo,release,receipt,current,run,calls=setup
    assert c.cleanup(repo,'9',receipt)['status']=='verified-ready-to-retain-release'
    assert not any(method=='DELETE' for _,method in calls)
    assert c.cleanup(repo,'9',receipt,apply=True)['status']=='release-retained-with-oss-links'
    assert [p for p,m in calls if m=='DELETE']==[f'repos/{repo}/releases/assets/1',f'repos/{repo}/releases/assets/2']
    assert not release['draft'] and release['prerelease']
    assert not current
    assert c.cleanup(repo,'9',receipt,apply=True)['removed_assets']==[]

@pytest.mark.parametrize('change',['running','published','extra','digest','foreign_url','incomplete'])
def test_cleanup_guards_keep_draft(setup,change):
    c,repo,release,receipt,current,run,calls=setup
    if change=='running':run['status']='in_progress'
    elif change=='published':release['draft']=False
    elif change=='extra':current['unknown.txt']=dict(size=1)
    elif change=='digest':next(iter(current.values()))['digest']='sha256:'+'c'*64
    elif change=='foreign_url':receipt['targets'][0]['uploads'][0]['url']='https://example.com/secret'
    elif change=='incomplete':receipt['status']='uploaded-and-public-head-verified'
    with pytest.raises(ValueError):c.cleanup(repo,'9',receipt,apply=True)
    assert not any(method=='DELETE' for _,method in calls)

def test_public_sha_failure_preserves_draft(setup,monkeypatch):
    c,repo,release,receipt,current,run,calls=setup
    def failed(*a):raise ValueError('download SHA differs')
    monkeypatch.setattr(c,'verify_public',failed)
    with pytest.raises(ValueError):c.cleanup(repo,'9',receipt,apply=True)
    assert not any(m=='DELETE' for _,m in calls)

def test_failed_description_save_retains_all_attachments(setup,monkeypatch):
    c,repo,release,receipt,current,run,calls=setup
    original=c.api
    def api(endpoint,method='GET',payload=None):
        if method=='PATCH':return copy.deepcopy(release)
        return original(endpoint,method,payload)
    monkeypatch.setattr(c,'api',api)
    with pytest.raises(ValueError,match='description not saved'):
        c.cleanup(repo,'9',receipt,apply=True)
    assert len(current)==2 and release['draft']
    assert not any(m=='DELETE' for _,m in calls)


def test_platform_checksums_do_not_replace_release_checksum_evidence(setup):
    c,repo,release,receipt,current,run,calls=setup
    prefix='qa-parser/candidates/candidate-aaaaaaaaaaaa-123'
    def item(key,digest):return dict(key=key,url='https://maiya-reader.oss-cn-beijing.aliyuncs.com/'+key,sha256=digest,bytes=12)
    receipt['targets'][0]['uploads'].append(item(prefix+'/windows-x64/SHA256SUMS.txt','c'*64))
    receipt['release_uploads']=[item(prefix+'/release/SHA256SUMS.txt','d'*64)]
    current['SHA256SUMS.txt']=dict(id=3,state='uploaded',digest='sha256:'+'d'*64,size=12)
    assert c.cleanup(repo,'9',receipt,apply=True)['status']=='release-retained-with-oss-links'
    assert not current
