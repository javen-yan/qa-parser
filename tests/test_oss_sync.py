import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def sync(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('sync_release_oss')

def test_owner_and_missing_settings(sync):
    with pytest.raises(ValueError, match='ACCESS_KEY_ID'):
        sync.settings({})
    owner = sync.identity('test/repo', '123', 'a'*40)
    owner.update(mode='manual', tag='candidate-aaaaaaaaaaaa-123')
    release = dict(body=sync.PREFIX+json.dumps(owner)+' -->', draft=True,
                   tag_name=owner['tag'], target_commitish=owner['commit'])
    assert sync.release_owner('test/repo', release)[1] == 'qa-parser/candidates/'+owner['tag']
    with pytest.raises(ValueError):
        sync.release_owner('other/repo', release)
    release['draft'] = False
    with pytest.raises(ValueError):
        sync.release_owner('test/repo', release)

class Missing(Exception):
    status = 404

class Bucket:
    def __init__(self, content=None):
        self.content = content
        self.calls = []
        self.headers = {}
    def head_object(self, key):
        if self.content is None:
            raise Missing()
        return SimpleNamespace(content_length=len(self.content), headers=self.headers)
    def get_object(self, key):
        return io.BytesIO(self.content)
    def put_object_from_file(self, key, path, headers):
        self.calls.append((key, headers))
        self.content = Path(path).read_bytes()
        self.headers = headers
        return SimpleNamespace(status=200, crc=42)

def test_immutable_upload_and_actual_retry_digest(sync, tmp_path):
    p=tmp_path/'test.zip'; p.write_bytes(b'content')
    key='qa-parser/candidates/candidate-abc-123/windows-x64/test.zip'
    bucket=Bucket()
    assert sync.upload_immutable(bucket,key,p)=='uploaded'
    assert bucket.calls[0][1]['x-oss-forbid-overwrite']=='true'
    assert bucket.calls[0][1]['Content-MD5']=='mgNkuembtIDdJeHwKEyFVQ=='
    assert sync.upload_immutable(bucket,key,p)=='already-identical'
    assert len(bucket.calls)==1
    bucket.content=b'corrupt'
    with pytest.raises(ValueError,match='actual content'):
        sync.upload_immutable(bucket,key,p)
    assert len(bucket.calls)==1
    with pytest.raises(ValueError,match='Unsafe'):
        sync.upload_immutable(bucket,'other/latest/test.zip',p)

def test_conflicting_existing_object(sync,tmp_path):
    p=tmp_path/'test.zip';p.write_bytes(b'new')
    bucket=Bucket(b'old')
    with pytest.raises(ValueError,match='refusing overwrite'):
        sync.upload_immutable(bucket,'qa-parser/candidates/candidate-abc-1/windows-x64/test.zip',p)
    assert not bucket.calls

def test_rejects_unsafe_public_url(sync):
    values=['id','secret','test-bucket','https://oss-cn-beijing.aliyuncs.com','https://user:pass@example.com','cn-beijing']
    with pytest.raises(ValueError,match='plain HTTPS'):
        sync.settings(dict(zip(sync.SECRET_NAMES,values)))

def test_prepare_download_gate_and_plan(sync,tmp_path,monkeypatch):
    from zipfile import ZipFile
    import hashlib
    archive=tmp_path/'fixture.zip'
    with ZipFile(archive,'w') as z:z.writestr('test.txt','synthetic')
    content=archive.read_bytes(); digest=hashlib.sha256(content).hexdigest()
    target='windows-x64'; owner=sync.identity('test/repo','123','a'*40)
    owner.update(mode='manual',tag='candidate-aaaaaaaaaaaa-123')
    release=dict(body=sync.PREFIX+json.dumps(owner)+' -->',draft=True,tag_name=owner['tag'],target_commitish=owner['commit'],html_url='https://github.com/test/repo/releases/123')
    metadata=dict(status='passed',target=target,commit=owner['commit'],native_smoke_passed=True,source_closure_complete=True,variant='light',zip_sha256=digest,bytes=len(content))
    payloads={1:content,2:json.dumps(metadata).encode()}
    items={name:dict(id=i,name=name,state='uploaded',size=len(payloads[i]),digest='sha256:'+hashlib.sha256(payloads[i]).hexdigest()) for i,name in [(1,f'qa-parser-{target}-candidate.zip'),(2,f'release-metadata-{target}.json')]}
    calls=[]
    def api(endpoint,output=None):
        if output is None:return release
        i=int(endpoint.rsplit('/',1)[1]); calls.append(i);output.write_bytes(payloads[i])
    monkeypatch.setattr(sync,'api',api);monkeypatch.setattr(sync,'assets',lambda *a:items)
    report=sync.prepare('test/repo','123',(target,),tmp_path/'plan',plan_only=True)
    assert report['status']=='planned' and calls==[2]
    report=sync.prepare('test/repo','123',(target,),tmp_path/'real')
    assert report['status']=='downloaded-and-verified' and calls==[2,2,1]
    items[f'qa-parser-{target}-candidate.zip']['digest']='sha256:'+'0'*64
    with pytest.raises(ValueError,match='checksum gate'):
        sync.prepare('test/repo','123',(target,),tmp_path/'bad')
    with pytest.raises(ValueError,match='missing'):
        sync.prepare('test/repo','123',sync.TARGETS,tmp_path/'all')


def test_public_download_checks_actual_bytes(sync,monkeypatch):
    import hashlib
    class Response(io.BytesIO):
        url='https://example.com/file.zip'
        status=200
        headers={'Content-Length':'3'}
    monkeypatch.setattr(sync,'urlopen',lambda *a,**k:Response(b'abc'))
    sync.verify_public(Response.url,3,hashlib.sha256(b'abc').hexdigest())
    with pytest.raises(ValueError,match='SHA-256'):
        sync.verify_public(Response.url,3,hashlib.sha256(b'xyz').hexdigest())
