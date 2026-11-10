"""Prepare a release from the actual checkout, independently of workflow revision."""
import os
from pathlib import Path
import re
import subprocess
import release_draft as draft


def main():
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    owner = draft.identity(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_RUN_ID'], source)
    ref = os.environ.get('SOURCE_REF', '')
    if ref:
        if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
            raise ValueError('Source tag builds must be dispatched from main')
        if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:rc|beta|alpha)\.\d+)?', ref):
            raise ValueError('Only existing version tags may be dispatched')
        owner['workflow_commit'] = os.environ['GITHUB_SHA']
        owner['source_ref'] = ref
        draft.check_tag_commit(owner['repository'], ref, source)
    elif source != os.environ['GITHUB_SHA']:
        raise ValueError('Checkout differs from triggering source')
    draft.ROOT = Path.cwd()
    draft.prepare(owner['repository'], owner, os.environ['RELEASE_MODE'], os.environ['RELEASE_TAG'])


if __name__ == '__main__':
    main()
