"""Synthetic Windows acceptance inputs must exercise real parser/export paths."""
import importlib.util
from pathlib import Path
import struct

import pytest

from qa_parser.core import run_document
from qa_parser.formula_images import _audit_wmf

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


smoke = module('windows_smoke')
public = module('check_public_tests')


def test_synthetic_smoke_uses_actual_omml_image_and_multiblank(tmp_path):
    inputs, png = smoke.create_inputs(tmp_path / '输入')
    for kind in ('choice', 'blank'):
        target = tmp_path / kind
        run_document(inputs[kind], kind, target)
        result = smoke.check_run(target, kind, png)
        assert len(result['questions']) == 1
    image = tmp_path / 'choice/export/images/0001.png'
    image.write_bytes(b'tampered')
    with pytest.raises(AssertionError, match='PNG bytes changed'):
        smoke.check_run(tmp_path / 'choice', 'choice', png)


def test_synthetic_wmf_has_valid_checksum_and_rejects_unknown_font():
    data = smoke.synthetic_wmf()
    checksum = 0
    for word in struct.unpack('<11H', data[:22]):
        checksum ^= word
    assert checksum == 0
    _audit_wmf(data)
    with pytest.raises(ValueError, match='unverified font'):
        _audit_wmf(data.replace(b'Times New Roman', b'Unknown Font!!!'))


def test_public_selector_explicitly_excludes_private_and_not_synthetic():
    assert public.exclusion_reason('test_libreoffice_preview.py', 'test_missing_runtime_preserves_original')
    assert public.exclusion_reason('test_document.py', 'test_reference_question_boundaries_and_resource_accounting')
    assert public.exclusion_reason('test_formulas.py', 'test_omml_fraction_preserves_numerator_denominator') is None
    # A stale registry must not silently cease excluding a renamed private case.
    import ast
    for filename, functions in public.PRIVATE_TESTS.items():
        definitions = {n.name for n in ast.walk(ast.parse((ROOT / 'tests' / filename).read_text(encoding='utf-8'))) if isinstance(n, ast.FunctionDef)}
        assert functions <= definitions, filename


def test_relocation_requires_complete_manifest_and_preserves_unpacked_bytes(tmp_path, monkeypatch):
    import hashlib
    import json
    project = tmp_path / 'project'
    web = project / 'src/qa_parser/web'
    web.mkdir(parents=True)
    (web / 'app.js').write_bytes(b'synthetic display script')
    monkeypatch.setattr(smoke, 'ROOT', project)
    package = tmp_path / 'built/题库转换'
    internal = package / '_internal'
    (internal / 'qa_parser/web').mkdir(parents=True)
    (internal / 'qa_parser/web/app.js').write_bytes((web / 'app.js').read_bytes())
    # Structural fixture only: this test validates packaging, not PE execution.
    pe = bytearray(70)
    pe[:2] = b'MZ'
    struct.pack_into('<I', pe, 60, 64)
    pe[64:68] = b'PE\0\0'
    struct.pack_into('<H', pe, 68, 0x8664)
    exe = package / '题库转换.exe'
    exe.write_bytes(pe)
    for name in ['wmf/bin/wmf2gd.exe', 'webview2/msedgewebview2.exe']:
        p = internal / name
        p.parent.mkdir(parents=True)
        p.write_bytes(pe)
    (internal / 'wmf/fonts').mkdir()
    (internal / 'wmf/fonts/fontmap').write_bytes(b'<fontmap/>')
    (internal / 'licenses').mkdir()
    (internal / 'licenses/NOTICE').write_bytes(b'synthetic test notice')
    files = []
    for folder in ('wmf', 'webview2', 'licenses'):
        for path in (internal / folder).rglob('*'):
            if path.is_file():
                files.append({'path': path.relative_to(internal).as_posix(), 'bytes': path.stat().st_size,
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = internal / 'resources-manifest.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'platform': 'win32', 'architecture': 'x64', 'files': files}))
    output = tmp_path / 'smoke'
    output.mkdir()
    relocated, before, evidence = smoke.relocate_bundle(exe, output)
    assert '中文 解压' in str(relocated)
    assert smoke.file_inventory(relocated.parent) == before
    assert evidence['native_manifest_files'] == 4
    (internal / 'licenses/NOTICE').write_bytes(b'tampered')
    with pytest.raises(AssertionError, match='Native resource changed'):
        smoke.relocate_bundle(exe, output)
