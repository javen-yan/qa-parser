from pathlib import Path
import json, hashlib, copy, importlib
from urllib.parse import urlparse, parse_qs
from zipfile import ZipFile
import pytest

class Fake:
 def __init__(self):self.release=None;self.files={};self.patches=[]
 def api(self,endpoint,*,method='GET',payload=None,file=None,content_type=None,output=None):
  if '/git/ref/tags/' in endpoint:return {'object':{'type':'commit','sha':'a'*40}}
  if endpoint.endswith('/releases') and method=='POST':
   assert self.release is None;self.release=dict(payload,id=123,html_url='https://github.com/test/repo/releases/123',published_at=None);return self.release
  if '/releases?' in endpoint:return [self.release] if self.release else []
  if '/releases/123/assets?' in endpoint and method=='GET':return [x[0] for x in self.files.values()]
  if endpoint.startswith('https://uploads.github.com/'):
   name=parse_qs(urlparse(endpoint).query)['name'][0];assert name not in self.files;b=file.read_bytes();item={'id':len(self.files)+1,'name':name,'state':'uploaded','size':len(b),'digest':'sha256:'+hashlib.sha256(b).hexdigest()};self.files[name]=(item,b);return item
  if '/releases/assets/' in endpoint and output is not None:
   id=int(endpoint.rsplit('/',1)[-1]);output.write_bytes(next(b for i,b in self.files.values() if i['id']==id));return
  if endpoint.endswith('/releases/123'):
   if method=='PATCH':
    self.patches.append(copy.deepcopy(payload));self.release.update(payload)
    if payload.get('draft') and 'tag_name' not in payload:self.release['tag_name']='untagged-server-generated'
   return self.release
  raise AssertionError((endpoint,method))

def build_inputs(root, d):
 for target in d.TARGETS:
  folder=root/target;folder.mkdir();archive=folder/f'qa-parser-{target}-candidate.zip'
  with ZipFile(archive,'w') as z:z.writestr('synthetic-check.txt',target)
  metadata=folder/'release-metadata.json';metadata.write_text(json.dumps({'status':'passed','target':target,'commit':'a'*40,'zip_sha256':d.sha(archive),'native_smoke_passed':True,'source_closure_complete':True,'variant':'light'}))
  yield target,archive,metadata

@pytest.mark.parametrize('mode', ['manual', 'tag'])
@pytest.mark.parametrize('keep_draft', [False, True])
def test_owned_release_full_control_flow(tmp_path, monkeypatch, mode, keep_draft):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    d = importlib.import_module('release_draft')
    monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    fake = Fake()
    monkeypatch.setattr(d, 'api', fake.api)
    owner = d.identity('test/repo', '99', 'a' * 40)
    version = d.tomllib.loads((d.ROOT / 'pyproject.toml').read_text())['project']['version']
    tag = 'v' + version + '-rc.1'
    d.prepare('test/repo', owner, mode, tag)
    with pytest.raises(ValueError, match='already exists'):
        d.prepare('test/repo', owner, mode, tag)
    with pytest.raises(ValueError, match='All three'):
        d.finalize('test/repo', '123', owner, tmp_path / 'partial')
    assert fake.release['draft'] and not fake.patches
    with pytest.raises(ValueError, match='another run'):
        d.owned('test/repo', '123', dict(owner, run_id='100'))
    for target, archive, metadata in build_inputs(tmp_path, d):
        d.upload_candidate('test/repo', '123', owner, target, archive, metadata)
        d.upload_candidate('test/repo', '123', owner, target, archive, metadata)
    d.finalize('test/repo', '123', owner, tmp_path / 'final', keep_draft=keep_draft)
    assert fake.release['draft'] == (mode == 'manual' or keep_draft)
    assert len(fake.files) == (9 if mode == 'manual' else 12)
    assert len(fake.patches) == 1
