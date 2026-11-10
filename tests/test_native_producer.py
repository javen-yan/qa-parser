import copy
import importlib
from pathlib import Path
import pytest


@pytest.fixture
def producer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    m = importlib.import_module('verify_native_producer')
    owner = dict(commit='a' * 40, run_id='123', mode='tag', tag='v0.3.3')
    run = dict(status='completed', conclusion='failure', head_sha=owner['commit'],
               head_branch='v0.3.3', event='push', run_attempt=1,
               path='.github/workflows/release.yml', head_repository=dict(full_name='test/repo'))
    jobs = [dict(id=i, name=name, status='completed', conclusion='success', runner_name='native',
                 steps=[dict(conclusion='success')]) for i, name in enumerate(sorted(m.NATIVE))]
    jobs.append(dict(id=9, name='finalize', status='completed', conclusion='failure', runner_name='', steps=[]))
    def read(endpoint):
        if '/jobs?' in endpoint: return dict(total_count=len(jobs), jobs=copy.deepcopy(jobs))
        return copy.deepcopy(run)
    return m, owner, run, jobs, read


def test_failed_delivery_reuses_verified_native_jobs_without_faking_run(producer):
    m, owner, run, jobs, read = producer
    result = m.verify('test/repo', owner, read=read)
    assert result['original_run_conclusion'] == 'failure'
    assert result['native_jobs_verified']


def test_current_delivery_checks_its_completed_native_dependencies(producer):
    m, owner, run, jobs, read = producer
    run.update(status='in_progress', conclusion=None)
    jobs[-1].update(name='deliver', status='in_progress', conclusion=None)
    assert m.verify('test/repo', owner, active_run='123', read=read)['native_jobs_verified']
    with pytest.raises(ValueError): m.verify('test/repo', owner, active_run='456', read=read)


@pytest.mark.parametrize('fault', ['source','repo','tag','event','missing','extra','native_failure','step_failure','native_running','delivery_running'])
def test_recovery_never_forgives_native_failure_or_untrusted_source(producer, fault):
    m, owner, run, jobs, read = producer
    if fault == 'source': run['head_sha'] = 'b' * 40
    elif fault == 'repo': run['head_repository']['full_name'] = 'evil/fork'
    elif fault == 'tag': run['head_branch'] = 'v0.3.4'
    elif fault == 'event': run['event'] = 'pull_request'
    elif fault == 'missing': jobs.pop(0)
    elif fault == 'extra': jobs.append(dict(jobs[0], name='unexpected'))
    elif fault == 'native_failure': jobs[0]['conclusion'] = 'failure'
    elif fault == 'step_failure': jobs[0]['steps'][0]['conclusion'] = 'failure'
    elif fault == 'native_running': jobs[0]['status'] = 'in_progress'
    elif fault == 'delivery_running': jobs[-1]['status'] = 'in_progress'
    with pytest.raises(ValueError): m.verify('test/repo', owner, read=read)


def test_source_assembly_uses_original_policy_not_current_main(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    p = importlib.import_module('publish_pipeline')
    assemble = p.source_assembler('012f57b88a9e5269d8ddb048fdc9ed0a55acd3e3', tmp_path)
    assert assemble.__globals__['ROOT'] == tmp_path / 'source-policy'
    assert (tmp_path / 'source-policy/pyproject.toml').is_file()


@pytest.mark.parametrize('fault', [None, 'moved_tag', 'source_ref', 'branch', 'workflow', 'native_failure'])
def test_dispatched_tag_binds_workflow_and_product_independently(producer, fault):
    m, owner, run, jobs, original_read = producer
    owner.update(workflow_commit='b' * 40, source_ref=owner['tag'])
    run.update(event='workflow_dispatch', head_branch='main', head_sha='b' * 40)
    if fault == 'source_ref': owner['source_ref'] = 'v9.9.9'
    if fault == 'branch': run['head_branch'] = 'feature'
    if fault == 'workflow': run['head_sha'] = 'c' * 40
    if fault == 'native_failure': jobs[0]['conclusion'] = 'failure'
    def read(endpoint):
        if '/git/ref/tags/' in endpoint:
            return {'object': {'type': 'tag', 'sha': 'd' * 40}}
        if '/git/tags/' in endpoint:
            return {'object': {'type': 'commit', 'sha': 'e' * 40 if fault == 'moved_tag' else owner['commit']}}
        return original_read(endpoint)
    if fault:
        with pytest.raises(ValueError): m.verify('test/repo', owner, read=read)
    else:
        result = m.verify('test/repo', owner, read=read)
        assert result['commit'] == 'a' * 40
        assert result['workflow_commit'] == 'b' * 40
