"""Preview fallback must not equate converter exit zero with formula fidelity."""
import io
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from qa_parser.formula_images import preview_to_png


def raster(format='PNG', blank=False):
    image = Image.new('RGB', (160, 50), 'white')
    if not blank:
        ImageDraw.Draw(image).text((10, 10), 'U = 2.5 V', fill='black')
    stream = io.BytesIO()
    image.save(stream, format=format)
    return stream.getvalue()


def record(function, payload=b''):
    payload += b'\0' * (len(payload) % 2)
    return struct.pack('<IH', 3 + len(payload) // 2, function) + payload


def wmf(font='Times New Roman', text=b'R1', charset=0):
    face = font.encode('ascii') + b'\0'
    font_payload = b'\0' * 13 + bytes([charset]) + b'\0' * 4 + face
    body = record(0x02FB, font_payload) + record(0x012D, struct.pack('<H', 0))
    body += record(0x0A32, struct.pack('<HHHH', 0, 0, len(text), 0) + text)
    body += record(0)
    return struct.pack('<HHHIHIH', 1, 9, 0x300, (18 + len(body)) // 2, 1, 100, 0) + body


def test_original_png_decodes_and_preserves_pixels():
    data = raster()
    result = preview_to_png(data, '.png')
    assert result['status'] == 'converted'
    assert (result['width'], result['height']) == (160, 50)
    a = Image.open(io.BytesIO(data)).convert('RGB')
    b = Image.open(io.BytesIO(result['png'])).convert('RGB')
    assert a.tobytes() == b.tobytes()


def test_jpeg_is_actually_transcoded_to_png():
    result = preview_to_png(raster('JPEG'), 'jpeg')
    assert result['status'] == 'converted'
    assert result['png'].startswith(b'\x89PNG\r\n\x1a\n')
    assert Image.open(io.BytesIO(result['png'])).format == 'PNG'


@pytest.mark.parametrize('data,extension', [
    (b'', 'png'), (b'not an image', 'png'),
    (raster(), 'jpeg'), (raster(blank=True), 'png'), (b'unhandled', 'emf'),
])
def test_invalid_blank_or_misnamed_preview_fails(data, extension):
    result = preview_to_png(data, extension)
    assert result['status'] == 'failed'
    assert result['png'] is None and result['reason']
    assert result['width'] == result['height'] == 0


@pytest.mark.parametrize('font,text', [('Symbol', b'W'), ('MT Extra', b'u'), ('Times New Roman', b'\xa9')])
def test_silent_glyph_loss_cases_rejected_before_renderer(monkeypatch, font, text):
    renderer = Mock()
    monkeypatch.setattr('qa_parser.formula_images.subprocess.run', renderer)
    result = preview_to_png(wmf(font, text), 'wmf')
    assert result['status'] == 'failed'
    assert 'font' in result['reason'] or 'glyph' in result['reason']
    renderer.assert_not_called()


def test_missing_renderer_returns_failure_without_download(monkeypatch):
    monkeypatch.delenv('QA_PARSER_WMF2GD', raising=False)
    monkeypatch.setattr('qa_parser.formula_images.shutil.which', lambda _: None)
    result = preview_to_png(wmf(), '.wmf')
    assert result['status'] == 'failed'
    assert 'not available' in result['reason']


def test_zero_exit_with_blank_output_is_not_success(monkeypatch):
    monkeypatch.setenv('QA_PARSER_WMF2GD', 'fake-renderer')
    def run(command, **kwargs):
        Path(command[command.index('-o') + 1]).write_bytes(raster(blank=True))
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr('qa_parser.formula_images.subprocess.run', run)
    result = preview_to_png(wmf(), 'wmf')
    assert result['status'] == 'failed' and 'blank' in result['reason']


def test_renderer_diagnostic_is_not_silently_accepted(monkeypatch):
    monkeypatch.setenv('QA_PARSER_WMF2GD', 'fake-renderer')
    monkeypatch.setattr('qa_parser.formula_images.subprocess.run',
                        lambda *a, **kw: SimpleNamespace(returncode=0, stderr=b'warning: missing glyph'))
    result = preview_to_png(wmf(), 'wmf')
    assert result['status'] == 'failed' and 'missing glyph' in result['reason']


def test_truncated_wmf_fails_before_renderer():
    result = preview_to_png(wmf()[:-3], 'wmf')
    assert result['status'] == 'failed' and 'length' in result['reason']


@pytest.mark.skipif(not shutil.which('wmf2gd'), reason='Optional libwmf runtime is not installed')
def test_real_original_mathtype_preview(tmp_path):
    from qa_parser.document import read_document
    sample = Path(__file__).resolve().parents[1] / 'reference' / 'MathType公式化学3.docx'
    doc = read_document(sample)
    tokens = [t for b in doc['blocks'] for t in b['tokens']
              if t.get('format') == 'mathtype' and t.get('preview')]
    successes = []
    # First physical quantity in this file is R_0: source MathType glyphs,
    # not a formula recreated from a guessed or failed LaTeX conversion.
    for token in tokens[:10]:
        result = preview_to_png(token['preview'], token['preview_ext'])
        if result['status'] == 'converted':
            successes.append(result)
    assert successes
    assert all(r['width'] > 10 and r['height'] > 10 for r in successes)


def test_symbol_charset_is_rejected_even_under_a_latin_font_name(monkeypatch):
    renderer = Mock()
    monkeypatch.setattr('qa_parser.formula_images.subprocess.run', renderer)
    result = preview_to_png(wmf('Times New Roman', b'W', charset=2), 'wmf')
    assert result['status'] == 'failed' and 'charset' in result['reason']
    renderer.assert_not_called()


def test_frozen_renderer_never_uses_developer_path(monkeypatch, tmp_path):
    from qa_parser.formula_images import _wmf_runtime
    import sys
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setenv('QA_PARSER_WMF2GD', '/opt/homebrew/bin/wmf2gd')
    assert _wmf_runtime(tmp_path) == (None, [])


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_frozen_fontmap_tracks_relocated_bundle(monkeypatch, tmp_path, platform):
    from qa_parser.formula_images import _wmf_runtime
    import sys
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(sys, 'platform', platform)
    bundle = tmp_path / '中文 relocated app'
    fonts = bundle / 'wmf/fonts'
    fonts.mkdir(parents=True)
    binary = bundle / 'wmf/bin' / ('wmf2gd.exe' if sys.platform == 'win32' else 'wmf2gd')
    binary.parent.mkdir()
    binary.write_bytes(b'placeholder')
    (fonts / 'fontmap').write_text('<fontmap><font metrics="/old/a.afm" glyphs="/old/a.t1" /></fontmap>')
    for name in ('a.afm', 'a.t1'):
        (fonts / name).write_bytes(b'font')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    executable, options = _wmf_runtime(tmp_path)
    assert executable == str(binary)
    font = ET.parse(tmp_path / 'fontmap.xml').getroot()[0]
    if platform == 'win32':
        assert font.attrib == {'metrics': 'fonts/a.afm', 'glyphs': 'fonts/a.t1'}
        assert '--wmf-sys-fontmap=fontmap.xml' in options
        assert (tmp_path / 'fonts/a.t1').read_bytes() == b'font'
    else:
        assert font.attrib == {'metrics': str(fonts / 'a.afm'), 'glyphs': str(fonts / 'a.t1')}
        assert '--wmf-sys-fontmap=' + str(tmp_path / 'fontmap.xml') in options
    assert '/old' not in (tmp_path / 'fontmap.xml').read_text()
