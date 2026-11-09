#!/usr/bin/env python3
"""Run one unified delivery job on an ephemeral local runner, then remove it.

Repository Secrets stay inside the Actions job. Registration tokens are only
passed via environment, never command arguments or printed response bodies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.request import urlopen
import uuid

REPO = 'javen-yan/qa-parser'
LABEL = 'qa-parser-release-local'


def gh(*args):
    attempts = 4 if args[0] == 'api' and '--method' not in args else 1
    for attempt in range(attempts):
        result = subprocess.run(['gh', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not result.returncode:
            break
        if attempt == attempts - 1:
            raise RuntimeError('GitHub request failed; no credential response is logged')
        time.sleep(attempt + 1)
    return json.loads(result.stdout) if args[0] == 'api' and result.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--retire-ids', default='')
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--runner-archive', type=Path, help='Previously downloaded official archive; official SHA is still required')
    args = parser.parse_args()
    if not re.fullmatch(r'[1-9][0-9]*', args.release_id) or (args.retire_ids and not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*)*', args.retire_ids)):
        raise ValueError('Explicit numeric release IDs are required')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Local launcher currently supports this macOS ARM64 host')
    existing = gh('api', f'repos/{REPO}/actions/runners')
    if any(LABEL in {label['name'] for label in r['labels']} for r in existing['runners']):
        raise ValueError('A delivery runner already exists; do not reuse another runner')
    candidates = gh('api', f'repos/{REPO}/actions/runners/downloads')
    item, = [r for r in candidates if r['os'] == 'osx' and r['architecture'] == 'arm64']
    if (not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256_checksum', ''))
            or not item['download_url'].startswith('https://github.com/actions/runner/releases/download/')):
        raise ValueError('Official runner URL/checksum is missing')
    root = Path(tempfile.mkdtemp(prefix='qa-release-runner-', dir='/tmp'))
    runner_id = None
    process = None
    args.evidence.mkdir(parents=True, exist_ok=True)
    name = 'qa-release-once-' + uuid.uuid4().hex[:12]
    try:
        archive = root / 'runner.tar.gz'
        if args.runner_archive:
            shutil.copyfile(args.runner_archive, archive)
        for attempt in range(0 if args.runner_archive else 4):
            try:
                with urlopen(item['download_url'], timeout=60) as response, archive.open('wb') as target:
                    shutil.copyfileobj(response, target)
                break
            except (OSError, EOFError):
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256_checksum']:
                raise ValueError('Runner SHA-256 differs')
        with tarfile.open(archive) as package:
            package.extractall(root, filter='data')
        archive.unlink()
        env = dict(os.environ)
        for key in tuple(env):
            if key.startswith('ALIYUN_OSS_') or key in ('GH_TOKEN', 'GITHUB_TOKEN'):
                env.pop(key)
        registration = gh('api', f'repos/{REPO}/actions/runners/registration-token', '--method', 'POST')
        config_env = dict(env, ACTIONS_RUNNER_INPUT_TOKEN=registration['token'])
        result = subprocess.run([str(root/'config.sh'), '--unattended', '--url', f'https://github.com/{REPO}',
            '--name', name, '--labels', LABEL, '--no-default-labels', '--ephemeral', '--disableupdate', '--work', '_work'],
            cwd=root, env=config_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        del config_env, registration
        if result.returncode:
            raise RuntimeError('Ephemeral runner configuration failed; credential output suppressed')
        runners = gh('api', f'repos/{REPO}/actions/runners')['runners']
        runner_id, = [r['id'] for r in runners if r['name'] == name]
        env['QA_RELEASE_EVIDENCE_DIR'] = str(args.evidence.resolve())
        env['QA_RELEASE_DOWNLOAD_CACHE_DIR'] = str(args.evidence.resolve().parent / 'download-cache')
        env['PATH'] = '/opt/homebrew/bin:' + env.get('PATH', '')
        run_query = f'repos/{REPO}/actions/workflows/release.yml/runs?event=workflow_dispatch&branch=main&per_page=20'
        previous = {r['id'] for r in gh('api', run_query)['workflow_runs']}
        with (args.evidence/'runner-lifecycle.log').open('wb') as log:
            process = subprocess.Popen([str(root/'run.sh')], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
            gh('workflow', 'run', 'release.yml', '--repo', REPO, '--ref', 'main',
               '-f', 'operation=resume', '-f', f'release_id={args.release_id}', '-f', 'delivery_runner=local',
               '-f', f'retire_ids={args.retire_ids}')
            print(f'Dispatched unified resume for Release {args.release_id}; ephemeral runner {runner_id}', flush=True)
            deadline = time.monotonic() + 125 * 60
            dispatched = None
            last_poll = 0
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError('Local delivery exceeded its bounded lifetime')
                if time.monotonic() - last_poll > 20:
                    last_poll = time.monotonic()
                    runs = gh('api', run_query)['workflow_runs']
                    matches = [r for r in runs if r['id'] not in previous and r.get('display_title') == f'resume release {args.release_id}']
                    if len(matches) > 1:
                        raise RuntimeError('Ambiguous concurrent resume dispatch')
                    if matches:
                        dispatched = matches[0]
                        (args.evidence/'dispatch.json').write_text(json.dumps({k:dispatched.get(k) for k in ('id','html_url','status','conclusion','head_sha')}))
                        if dispatched['status'] == 'completed' and dispatched['conclusion'] != 'success':
                            raise RuntimeError('Unified job failed; existing packages retained for resume')
                time.sleep(5)
            if process.returncode:
                raise RuntimeError('Runner exited unsuccessfully; inspect masked Actions job logs')
    finally:
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
        removed = False
        try:
            runners = gh('api', f'repos/{REPO}/actions/runners')['runners']
            for runner in runners:
                if runner['name'] == name:
                    gh('api', f"repos/{REPO}/actions/runners/{runner['id']}", '--method', 'DELETE')
            removed = not any(r['name'] == name for r in gh('api', f'repos/{REPO}/actions/runners')['runners'])
            if not removed:
                raise RuntimeError('Runner registration cleanup requires attention')
        finally:
            shutil.rmtree(root)
            (args.evidence/'runner-cleanup.json').write_text(json.dumps({'runner_name':name, 'runner_id':runner_id,
                'registration_removed':removed, 'temporary_directory_removed':not root.exists()}))
        print('Ephemeral runner registration and temporary files removed', flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('Local release stopped: ' + (str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__))
        raise SystemExit(1)
