import hashlib
import importlib
import io
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError
import pytest


@pytest.mark.parametrize('fault', [None, 'range', 'prefix', 'redirect'])
def test_official_ranges_preserve_prefix_and_require_full_sha(monkeypatch, tmp_path, fault):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    d = importlib.import_module('release_download')
    data = b'abcdefghijklmnopqrstuvwxyz'
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setenv('GH_TOKEN', 'secret-test-token')
    monkeypatch.setenv('QA_RELEASE_DOWNLOAD_CACHE_DIR', str(tmp_path/'cache'))
    monkeypatch.setattr(d, 'BLOCK_SIZE', 10)
    (tmp_path/'cache').mkdir()
    prefix = tmp_path/'cache'/f'{digest}.prefix'
    prefix.write_bytes(b'xxxx' if fault == 'prefix' else data[:4])
    class Opener:
        def open(self, request, timeout):
            assert request.get_header('Authorization') == 'Bearer secret-test-token'
            host = 'evil.invalid' if fault == 'redirect' else 'release-assets.githubusercontent.com'
            raise HTTPError('api', 302, '', {'Location': f'https://{host}/asset?secret-signature'}, None)
    monkeypatch.setattr(d, 'build_opener', lambda *a: Opener())
    ranges = []
    def run(args, input, capture_output):
        assert 'secret-signature' not in ' '.join(args) and 'secret-test-token' not in ' '.join(args)
        assert b'secret-signature' in input and b'secret-test-token' not in input
        a, b = map(int, args[args.index('--range')+1].split('-'))
        ranges.append((a, b))
        Path(args[args.index('--output')+1]).write_bytes(data[a:b+1])
        return SimpleNamespace(returncode=0,stdout=f'Content-Range: bytes {a}-{b}/{len(data)}\r\n\n{200 if fault=="range" else 206}'.encode())
    monkeypatch.setattr(d.subprocess, 'run', run)
    item = dict(name='native.zip', size=len(data), digest='sha256:'+digest)
    out = tmp_path/'native.zip'
    if fault:
        with pytest.raises(ValueError): d.download('repos/test/repo/releases/assets/1', item, out)
        assert not out.exists()
    else:
        d.download('repos/test/repo/releases/assets/1', item, out)
        assert out.read_bytes() == data and sorted(ranges) == [(4,13),(14,23),(24,25)]
        ranges.clear()
        d.download('repos/test/repo/releases/assets/1', item, out)
        assert not ranges  # verified cache avoids re-downloading
