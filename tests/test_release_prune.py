import copy
import importlib
import json
from pathlib import Path
import pytest

@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    p=importlib.import_module('prune_releases');commit='a'*40;repo=p.REPO;calls=[];active=[False]
    def release(id,mode,tag):
        owner=dict(schema_version=1,repository=repo,commit=commit,run_id=str(id),mode=mode,tag=tag)
        return dict(id=id,tag_name=tag,target_commitish=commit,draft=mode=='manual',prerelease=mode=='manual',assets=[],body=p.PREFIX+json.dumps(owner)+' -->',html_url='https://github.com/'+repo+'/releases/tag/'+tag)
    rows={'10':release(10,'tag','v0.3.0'),'1':release(1,'manual','candidate-aaaaaaaaaaaa-1'),'2':release(2,'manual','candidate-aaaaaaaaaaaa-2')}
    receipt=dict(status='uploaded-and-public-sha256-verified',repository=repo,release_id='10',commit=commit,tag='v0.3.0',targets=[])
    rows['10']['body']+=f'<!-- qa-parser-oss-delivery:10:{commit} -->'
    for target in p.TARGETS:
        key=f'qa-parser/releases/v0.3.0/{target}/test.zip';url=p.BASE+key
        receipt['targets'].append(dict(target=target,uploads=[dict(key=key,url=url,bytes=1,sha256='b'*64)]));rows['10']['body']+=url
    def api(endpoint,method='GET'):
        calls.append((endpoint,method))
        if method=='DELETE':
            assert '/releases/' in endpoint and '/git/' not in endpoint
            del rows[endpoint.rsplit('/',1)[1]];return None
        if 'actions/runs?' in endpoint:return {'total_count':int(active[0]),'workflow_runs':[dict(id=888,path='.github/workflows/release.yml')] if active[0] else []}
        if '/actions/runs/' in endpoint:return {'status':'completed'}
        if '/git/ref/' in endpoint:return {'object':{'type':'commit','sha':commit}}
        if '/releases?' in endpoint:return copy.deepcopy(list(rows.values()))
        raise AssertionError(endpoint)
    monkeypatch.setattr(p,'api',api);monkeypatch.setattr(p,'read_api',api);monkeypatch.setattr(p,'verify_public',lambda *a:None)
    return p,rows,receipt,calls,active

def test_exact_old_set_removed_source_and_formal_release_untouched(scenario,tmp_path):
    p,rows,receipt,calls,active=scenario
    result=p.prune('10',{'1','2'},receipt,apply=True,report_path=tmp_path/'report.json')
    assert result['status']=='only-formal-release-retained' and set(rows)=={'10'}
    assert [endpoint for endpoint,method in calls if method=='DELETE']==[f'repos/{p.REPO}/releases/1',f'repos/{p.REPO}/releases/2']
    assert p.prune('10',{'1','2'},receipt,apply=True)['already_absent']==['1','2']


@pytest.mark.parametrize('tag,mirrored,allowed', [('v0.2.9',True,True),('v0.3.0',True,False),('v0.4.0',True,False),('v0.2.9',False,False)])
def test_retire_only_explicit_older_mirrored_formal_release(scenario,tag,mirrored,allowed):
    p,rows,receipt,calls,active=scenario
    owner=dict(schema_version=1,repository=p.REPO,commit='a'*40,run_id='1',mode='tag',tag=tag)
    rows['1'].update(draft=False,prerelease=False,tag_name=tag,body=p.PREFIX+json.dumps(owner)+' -->')
    if mirrored:rows['1']['body']+='<!-- qa-parser-oss-delivery:1:'+owner['commit']+' -->'
    if allowed:
        assert p.prune('10',{'1','2'},receipt,apply=True)['status']=='only-formal-release-retained'
    else:
        with pytest.raises(ValueError):p.prune('10',{'1','2'},receipt,apply=True)
        assert not any(method=='DELETE' for _,method in calls)

@pytest.mark.parametrize('failure',['active','unexpected','prerelease','attachments','hash'])
def test_prune_failures_preserve_history(scenario,monkeypatch,failure):
    p,rows,receipt,calls,active=scenario
    if failure=='active':active[0]=True
    elif failure=='unexpected':rows['99']=dict(rows['1'],id=99)
    elif failure=='prerelease':rows['10']['prerelease']=True
    elif failure=='attachments':rows['10']['assets']=[{'id':100}]
    else:
        def fail(*a):raise ValueError('public SHA mismatch')
        monkeypatch.setattr(p,'verify_public',fail)
    with pytest.raises(ValueError):p.prune('10',{'1','2'},receipt,apply=True)
    assert not any(method=='DELETE' for _,method in calls)
