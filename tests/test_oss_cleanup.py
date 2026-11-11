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
        if '/git/ref/tags/' in endpoint:return {'object':{'type':'commit','sha':commit}}
        if method=='DELETE':
            assert '/releases/assets/' in endpoint
            assert '阿里云下载' in release['body']
            asset_id=int(endpoint.rsplit('/',1)[1])
            for name in list(current):
                if current[name]['id']==asset_id:del current[name]
            return None
        if method=='PATCH':
            release.update(payload)
            for asset in current.values():asset['browser_download_url']='regenerated-draft-link'
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


def test_wrong_source_tag_blocks_attachment_cleanup(setup,monkeypatch):
    c,repo,release,receipt,current,run,calls=setup
    original=c.api
    def api(endpoint,method='GET',payload=None):
        if '/git/ref/tags/' in endpoint:return {'object':{'type':'commit','sha':'c'*40}}
        return original(endpoint,method,payload)
    monkeypatch.setattr(c,'api',api)
    with pytest.raises(ValueError,match='Source tag differs'):
        c.cleanup(repo,'9',receipt,apply=True)
    assert len(current)==2
    assert not any(m in ('PATCH','DELETE') for _,m in calls)


@pytest.mark.parametrize('message,retries',[('TLS handshake timeout',3),('HTTP 403',1)])
def test_only_transient_reads_retry_with_a_bound(monkeypatch,message,retries):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    c=importlib.import_module('cleanup_oss_draft');calls=[]
    monkeypatch.setattr(c.time,'sleep',lambda seconds:None)
    def fail(*args):calls.append(args);raise RuntimeError(message)
    with pytest.raises(RuntimeError):c.retry_read(fail,'read-only-endpoint')
    assert len(calls)==retries


def test_formal_release_preserves_stable_state_and_removes_duplicate_archive_aliases(setup):
    c,repo,release,receipt,current,run,calls=setup
    owner=json.loads(release['body'].split(':',1)[1].split(' -->')[0])
    owner.update(mode='tag',tag='v0.3.0')
    release.update(draft=False,prerelease=False,tag_name='v0.3.0',body='<!-- qa-parser-release-owner:'+json.dumps(owner)+' -->')
    run['conclusion']='success';receipt.update(draft=False,targets=[],release_uploads=[]);current.clear()
    def evidence(name,folder):
        key='qa-parser/releases/v0.3.0/'+folder+'/'+name
        item=dict(key=key,url='https://maiya-reader.oss-cn-beijing.aliyuncs.com/'+key,sha256='b'*64,bytes=12)
        current[name]=dict(id=len(current)+1,state='uploaded',digest='sha256:'+'b'*64,size=12)
        return item
    for target in c.TARGETS:
        name=f'qa-parser-v0.3.0-{target}-light.zip'
        upload=evidence(name,target)
        evidence(f'qa-parser-{target}-candidate.zip',target)
        meta=evidence(f'release-metadata-{target}.json',target)
        receipt['targets'].append(dict(target=target,filename=name,uploads=[upload,meta]))
    for name in ('release-manifest.json','SHA256SUMS.txt','RELEASE_NOTES.md'):
        receipt['release_uploads'].append(evidence(name,'release'))
    result=c.cleanup(repo,'9',receipt,apply=True)
    assert len(result['removed_assets'])==12 and not current
    assert release['draft'] is False and release['prerelease'] is False
    assert release['make_latest']=='true' and '正式发布。' in release['body']
    assert not any(endpoint.endswith('/releases/9') and method=='DELETE' for endpoint,method in calls)


@pytest.mark.parametrize('fresh', [False, True])
def test_only_fresh_in_process_verification_skips_second_full_download(setup, monkeypatch, fresh):
    c, repo, release, receipt, current, run, calls = setup
    checks = []
    monkeypatch.setattr(c, 'verify_public', lambda *a, **kw: checks.append((a, kw)))
    c.cleanup(repo, '9', receipt, apply=True, public_download_verified=fresh)
    assert len(checks) == 2
    for args, kwargs in checks:
        if fresh:
            assert len(args) == 2 and kwargs == {'metadata_digest': 'b' * 64}
        else:
            assert len(args) == 3 and args[2] == 'b' * 64 and not kwargs


def test_fresh_verification_still_rejects_changed_public_metadata(setup, monkeypatch):
    c, repo, release, receipt, current, run, calls = setup
    def changed(*args, **kwargs):
        assert kwargs == {'metadata_digest': 'b' * 64}
        raise ValueError('metadata mismatch')
    monkeypatch.setattr(c, 'verify_public', changed)
    with pytest.raises(ValueError, match='metadata mismatch'):
        c.cleanup(repo, '9', receipt, apply=True, public_download_verified=True)
    assert release['draft'] and len(current) == 2
    assert not any(method in ('PATCH', 'DELETE') for _, method in calls)
