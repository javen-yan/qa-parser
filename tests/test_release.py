import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('prepare_release')


def inputs(tmp_path):
    incoming = tmp_path / 'input'
    for target in ['windows-x64', 'macos-arm64', 'macos-x86_64']:
        folder = incoming / target
        folder.mkdir(parents=True)
        archive = folder / f'qa-parser-{target}-candidate.zip'
        with ZipFile(archive, 'w') as z:
            z.writestr('synthetic.txt', 'synthetic test only')
        (folder / 'release-metadata.json').write_text(json.dumps({
            'status': 'passed', 'target': target, 'commit': 'abc', 'zip_sha256': release.digest(archive),
            'variant': 'light', 'native_smoke_passed': True, 'source_closure_complete': True}))
    return incoming


def test_release_requires_complete_same_commit_set(tmp_path):
    incoming = inputs(tmp_path)
    result = release.assemble(incoming, tmp_path / 'out', 'v0.3.0-rc.1', 'abc')
    assert len(result['artifacts']) == 3 and result['prerelease']
    assert (tmp_path / 'out/SHA256SUMS.txt').read_text().count('\n') == 3
    assert '900/900' in (tmp_path / 'out/RELEASE_NOTES.md').read_text()


@pytest.mark.parametrize('mutation', ['missing', 'hash', 'commit', 'source'])
def test_release_fails_before_output_on_incomplete_evidence(tmp_path, mutation):
    incoming = inputs(tmp_path)
    metadata = incoming / 'windows-x64/release-metadata.json'
    data = json.loads(metadata.read_text())
    if mutation == 'missing':
        (incoming / 'windows-x64/qa-parser-windows-x64-candidate.zip').unlink()
    elif mutation == 'hash':
        data['zip_sha256'] = 'wrong'
    elif mutation == 'commit':
        data['commit'] = 'other'
    else:
        data['source_closure_complete'] = False
    metadata.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        release.assemble(incoming, tmp_path / 'out', 'v0.3.0-rc.1', 'abc')
    assert not (tmp_path / 'out').exists()


def test_stable_release_not_approved_and_version_mismatch(tmp_path):
    with pytest.raises(ValueError, match='Stable publication blocked'):
        release.assemble(tmp_path, tmp_path / 'out', 'v0.3.0', 'abc')
    with pytest.raises(ValueError, match='match pyproject'):
        release.assemble(tmp_path, tmp_path / 'out', 'v9.9.9-rc.1', 'abc')


def test_remote_asset_mismatch_never_publishes_draft(tmp_path, monkeypatch):
    publisher = load('publish_release')
    (tmp_path / 'release-manifest.json').write_text(json.dumps({'tag': 'v0.3.0-rc.1', 'prerelease': True}))
    (tmp_path / 'RELEASE_NOTES.md').write_text('synthetic')
    calls = []
    def fake(*args):
        calls.append(args)
        return json.dumps({'draft': True, 'assets': []}) if args[0] == 'api' else ''
    monkeypatch.setattr(publisher, 'gh', fake)
    with pytest.raises(RuntimeError, match='Incomplete release'):
        publisher.publish(tmp_path, 'example/test')
    assert not any(c[:2] == ('release', 'edit') for c in calls)
