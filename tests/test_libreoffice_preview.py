"""Strict original-coordinate replay, including real Q62 source drawings."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from qa_parser.libreoffice_preview import audit_wmf, preview_to_png

FIXTURES = Path(__file__).parent / 'fixtures/original-wmf'


def test_real_original_font_glyph_records():
    evidence = audit_wmf((FIXTURES / 'F000318.wmf').read_bytes())
    assert [v['text'] for v in evidence][-2:] == ['Δ', '↑']
    for name in ['F000319', 'F000320']:
        evidence = audit_wmf((FIXTURES / (name + '.wmf')).read_bytes())
        assert [v['text'] for v in evidence][-2:] == ['点', '燃']
        assert [v['charset'] for v in evidence][-2:] == [134, 134]


def test_unknown_font_fails_before_runtime():
    data = (FIXTURES / 'F000318.wmf').read_bytes().replace(b'Times New Roman', b'Wrong FontName!')
    with patch('qa_parser.libreoffice_preview.runtime_paths') as runtime:
        result = preview_to_png(data)
    assert result['status'] == 'failed'
    assert 'Unverified WMF font' in result['reason']
    runtime.assert_not_called()


def test_missing_runtime_preserves_original(monkeypatch):
    monkeypatch.delenv('QA_PARSER_LIBREOFFICE_ROOT', raising=False)
    result = preview_to_png((FIXTURES / 'F000318.wmf').read_bytes())
    assert result['status'] == 'failed'
    assert result['png'] is None
    assert 'not available' in result['reason']


def test_unverified_symbol_rejected():
    data = (FIXTURES / 'F000318.wmf').read_bytes()
    # Mutate only the known Symbol text run, keeping WMF record sizes valid.
    import struct
    pos = 40
    while pos < len(data):
        words, op = struct.unpack_from('<IH', data, pos)
        if op == 0x0A32:
            count = struct.unpack_from('<H', data, pos+10)[0]
            if count == 1 and data[pos+14:pos+15] == b'\xad':
                data = data[:pos+14] + b'\xae' + data[pos+15:]
                break
        pos += words*2
    with pytest.raises(ValueError, match='Unverified Symbol'):
        audit_wmf(data)


@pytest.mark.skipif(not os.environ.get('QA_PARSER_LIBREOFFICE_ROOT'), reason='Optional LibreOffice runtime not provisioned')
@pytest.mark.parametrize('name', ['F000318', 'F000319', 'F000320'])
def test_real_offline_replay(name):
    result = preview_to_png((FIXTURES / (name + '.wmf')).read_bytes())
    assert result['status'] == 'converted', result['reason']
    assert 100 < result['width'] < 1000
    assert 40 < result['height'] < 300
    assert result['font_evidence'] == audit_wmf((FIXTURES / (name + '.wmf')).read_bytes())


def test_cjk_coverage_is_not_tied_to_sample_words():
    data = (FIXTURES / 'F000319.wmf').read_bytes().replace(bytes.fromhex('b5e3'), '加'.encode('gbk'))
    evidence = audit_wmf(data)
    assert evidence[-2]['text'] == '加'


def test_missing_cjk_coverage_blocks_before_runtime():
    with patch('qa_parser.libreoffice_preview._cjk_covered', return_value=False), patch('qa_parser.libreoffice_preview.runtime_paths') as runtime:
        result = preview_to_png((FIXTURES / 'F000319.wmf').read_bytes())
    assert result['status'] == 'failed'
    assert 'absent' in result['reason']
    runtime.assert_not_called()
