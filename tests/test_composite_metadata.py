import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('composite_candidate_metadata', Path(__file__).parents[1] / 'scripts/candidate_metadata.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


@pytest.mark.parametrize('runs,accepted', [
    ({'development': {'status': 'passed', 'semantic_sha256': 'a'}}, False),
    ({'development': {'status': 'passed', 'semantic_sha256': 'a'}, 'frozen': {'status': 'passed', 'semantic_sha256': 'b'}}, False),
    ({'development': {'status': 'passed', 'semantic_sha256': 'a'}, 'frozen': {'status': 'failed', 'semantic_sha256': 'a'}}, False),
    ({'development': {'status': 'passed', 'semantic_sha256': 'a'}, 'frozen': {'status': 'passed', 'semantic_sha256': 'a'}}, True),
])
def test_candidate_requires_actual_matching_frozen_business_execution(tmp_path, runs, accepted):
    archive, smoke, composite, output = [tmp_path / name for name in ('app.zip', 'smoke.json', 'composite.json', 'metadata.json')]
    archive.write_bytes(b'synthetic archive identity')
    smoke.write_text(json.dumps({'status': 'passed', 'gui': {'status': 'passed'}}))
    composite.write_text(json.dumps({'status': 'passed', 'runs': runs}))
    if accepted:
        metadata.write_metadata(archive, smoke, 'macos-arm64', output, True, composite=composite)
        assert json.loads(output.read_text())['composite_validation']['runs'] == runs
    else:
        with pytest.raises(ValueError, match='Composite'):
            metadata.write_metadata(archive, smoke, 'macos-arm64', output, True, composite=composite)
        assert not output.exists()
