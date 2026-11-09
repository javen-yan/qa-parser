import hashlib
import importlib
import io
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
    class Response(io.BytesIO):
        status = 206
    def urlopen(request, timeout):
        assert request.get_header('Authorization') is None
        a, b = map(int, request.get_header('Range').removeprefix('bytes=').split('-'))
        ranges.append((a, b))
        result = Response(data[a:b+1])
        result.headers = {'Content-Range': f'bytes {a}-{b}/{len(data)}'}
        if fault == 'range': result.status = 200
        return result
    monkeypatch.setattr(d, 'urlopen', urlopen)
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
