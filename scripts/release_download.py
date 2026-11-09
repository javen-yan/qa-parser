"""Bounded official GitHub ranges; tokens and signed URLs stay in memory."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BLOCK_SIZE = 8 * 1024**2


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(endpoint, item, output):
    digest = item.get('digest', '').removeprefix('sha256:')
    size = item.get('size', 0)
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or not 0 < size < 2 * 1024**3:
        raise ValueError('Official asset checksum or size missing')
    cache = Path(os.environ.get('QA_RELEASE_DOWNLOAD_CACHE_DIR', str(output.parent / '.asset-cache')))
    cache.mkdir(parents=True, exist_ok=True)
    complete = cache / (digest + '.zip')
    if complete.exists():
        if complete.stat().st_size != size or sha(complete) != digest:
            raise ValueError('Cached asset differs from official digest')
        shutil.copyfile(complete, output)
        return
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        result = subprocess.run(['gh', 'auth', 'token'], capture_output=True)
        if result.returncode:
            raise RuntimeError('GitHub authentication unavailable for asset download')
        token = result.stdout.decode().strip()
    request = Request('https://api.github.com/' + endpoint, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/octet-stream',
        'X-GitHub-Api-Version': '2026-03-10'})
    url = None
    for attempt in range(4):
        try:
            response = build_opener(NoRedirect).open(request, timeout=30)
            response.close()
            raise ValueError('Expected official asset redirect')
        except HTTPError as error:
            if error.code == 302:
                url = error.headers['Location']; break
            if error.code not in (502, 503, 504) or attempt == 3:
                raise RuntimeError('Official asset redirect request failed') from None
        except OSError:
            if attempt == 3:
                raise RuntimeError('Official asset redirect connection failed') from None
        time.sleep(attempt + 1)
    del token, request
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
        raise ValueError('Asset redirect is not an official GitHub HTTPS host')
    parts = cache / digest
    parts.mkdir(exist_ok=True)
    prefix = cache / (digest + '.prefix')
    start = prefix.stat().st_size if prefix.exists() else 0
    if start > size:
        raise ValueError('Saved partial asset exceeds source size')
    blocks = [(a, min(a + BLOCK_SIZE, size) - 1) for a in range(start, size, BLOCK_SIZE)]
    def fetch(block):
        a, b = block
        part = parts / f'{a}-{b}'
        if part.exists() and part.stat().st_size == b - a + 1:
            return part
        temporary = part.with_suffix('.partial')
        result = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--config', '-',
            '--range', f'{a}-{b}', '--connect-timeout', '15', '--max-time', '180',
            '--retry', '3', '--retry-all-errors', '--retry-delay', '1',
            '--dump-header', '-', '--output', str(temporary), '--write-out', '\n%{http_code}'],
            input=('url = ' + json.dumps(url) + '\n').encode(), capture_output=True)
        headers = result.stdout.decode(errors='replace')
        status = headers.strip().splitlines()[-1] if headers.strip() else 'unknown'
        if result.returncode:
            raise RuntimeError(f'Official asset range failed (curl {result.returncode}, HTTP {status})')
        ranges = re.findall(r'(?im)^content-range:\s*([^\r\n]+)', headers)
        if status != '206' or not ranges or ranges[-1] != f'bytes {a}-{b}/{size}':
            raise ValueError('Server did not honor the exact requested range')
        if temporary.stat().st_size != b - a + 1:
            raise ValueError('Incomplete official asset range')
        temporary.replace(part)
        return part
    with ThreadPoolExecutor(max_workers=8) as pool:
        downloaded = list(pool.map(fetch, blocks))
    assembled = complete.with_suffix('.partial')
    with assembled.open('wb') as stream:
        for path in ([prefix] if start else []) + downloaded:
            with path.open('rb') as part:
                shutil.copyfileobj(part, stream)
    if assembled.stat().st_size != size or sha(assembled) != digest:
        raise ValueError('Complete official asset SHA-256 mismatch; no output published')
    assembled.replace(complete)
    shutil.copyfile(complete, output)
    print(f'Verified asset {item["name"]}: {size} bytes, SHA-256 {digest}', flush=True)
