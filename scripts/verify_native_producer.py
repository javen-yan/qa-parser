"""Verify native jobs directly, independently of a failed downstream delivery.

The original run conclusion is retained as evidence, never rewritten. A resume
may reuse successful platform jobs, but cannot forgive a failed native build.
"""
import re
from release_draft import api

NATIVE = {'prepare', 'windows', 'macos (arm64, macos-15)', 'macos (x86_64, macos-15-intel)'}


def verify(repo, owner, *, active_run=None, read=api):
    run_id = str(owner['run_id'])
    run = read(f'repos/{repo}/actions/runs/{run_id}')
    if (run.get('head_sha') != owner['commit'] or run.get('path') != '.github/workflows/release.yml'
            or run.get('head_repository', {}).get('full_name') != repo):
        raise ValueError('Native producer repository/source/workflow differs')
    formal = owner['mode'] == 'tag'
    if formal:
        if run.get('event') != 'push' or run.get('head_branch') != owner['tag']:
            raise ValueError('Formal assets must come from the exact source tag push')
    elif run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main':
        raise ValueError('Manual candidate must originate on main')
    if run.get('status') != 'completed' and not (active_run == run_id and run.get('status') == 'in_progress'):
        raise ValueError('Another producer is still running')
    listing = read(f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100')
    jobs = listing.get('jobs', [])
    names = [j['name'] for j in jobs]
    if (listing.get('total_count') != len(jobs) or len(names) != len(set(names))
            or not NATIVE <= set(names) or set(names) - NATIVE not in ({'finalize'}, {'deliver'})):
        raise ValueError('Incomplete or unexpected producer job set')
    for job in jobs:
        if job['name'] in NATIVE:
            if (job.get('status') != 'completed' or job.get('conclusion') != 'success'
                    or not job.get('runner_name') or not job.get('steps')
                    or any(step.get('conclusion') not in ('success', 'skipped') for step in job['steps'])):
                raise ValueError('Native build or validation did not succeed')
        elif active_run != run_id and job.get('status') != 'completed':
            raise ValueError('Previous delivery is still running')
    return {'schema_version': 1, 'repository': repo, 'commit': owner['commit'],
            'producer_run_id': run_id, 'original_run_status': run['status'],
            'original_run_conclusion': run.get('conclusion'), 'run_attempt': run.get('run_attempt'),
            'native_jobs_verified': True,
            'jobs': [{k: j.get(k) for k in ('id', 'name', 'status', 'conclusion', 'runner_name')} for j in jobs]}
