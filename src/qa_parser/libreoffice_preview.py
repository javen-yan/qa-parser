"""Optional original-WMF replay through a separately bundled LibreOffice.

Restricted to verified original WMF record/font/glyph combinations; no MTEF
interpretation, formula reconstruction, system font lookup or runtime downloads.
"""
from __future__ import annotations
import io
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

from .formula_images import MAX_INPUT_BYTES, _failed, _validate_png

ENGINE = 'LibreOffice/original-wmf-explicit-fonts/1'

def _cjk_covered(text: str) -> bool:
    directory = Path(__file__).parent / 'web/vendor/fonts'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    font = directory / 'NotoSansCJKsc-Regular.otf'
    if manifest.get('path') != font.name or hashlib.sha256(font.read_bytes()).hexdigest() != manifest.get('sha256'):
        raise ValueError('Bundled CJK font does not match its coverage manifest')
    ranges = manifest['unicode_ranges']
    return all(any(low <= ord(c) <= high for low, high in ranges) for c in text)


def audit_wmf(data: bytes) -> list[dict]:
    """Allow only replay records and encoded glyphs verified on original WMFs.

    Unknown glyphs remain unresolved even if LibreOffice can produce an image.
    This gate is independent of, and does not relax, the libwmf adapter gate.
    """
    evidence = []
    offset = 22 if data.startswith(b'\xd7\xcd\xc6\x9a') else 0
    if len(data) < offset + 18:
        raise ValueError('WMF header is truncated')
    file_type, header_size, version, words, handles = struct.unpack_from('<HHHIH', data, offset)
    if file_type not in (1, 2) or header_size != 9 or version not in (0x100, 0x300):
        raise ValueError('Unrecognized WMF header')
    if words * 2 != len(data) - offset:
        raise ValueError('WMF declared length differs from original bytes')
    if handles > 4096:
        raise ValueError('WMF object table exceeds supported bounds')
    offset += 18
    objects: dict[int, tuple[str, int] | None] = {}
    font = None
    charset = None
    saw_end = False
    saw_drawing = False
    records = 0
    # State and simple line records verified on the real previews. Escape is
    # separately restricted to MFCOMMENT, which carries MathType metadata.
    plain = {0x0102, 0x0201, 0x012E, 0x020B, 0x020C, 0x0209, 0x0214, 0x0213}
    while offset + 6 <= len(data):
        records += 1
        if records > 100_000:
            raise ValueError('WMF record count exceeds supported bounds')
        length, function = struct.unpack_from('<IH', data, offset)
        end = offset + length * 2
        if length < 3 or end > len(data):
            raise ValueError('WMF record is truncated or has invalid length')
        payload = data[offset + 6:end]
        if function == 0:
            if length != 3 or end != len(data):
                raise ValueError('WMF EOF or trailing data is invalid')
            saw_end = True
            break
        if function in (0x02FB, 0x02FA, 0x02FC):
            handle = next((n for n in range(handles) if n not in objects), None)
            if handle is None:
                raise ValueError('WMF object table is inconsistent')
            if function == 0x02FB:
                if len(payload) < 19:
                    raise ValueError('WMF font record is truncated')
                raw_font = payload[18:].split(b'\0', 1)[0]
                # A defined but unused font does not affect the drawn content.
                try:
                    name = raw_font.decode('ascii').strip().lower()
                except UnicodeDecodeError:
                    name = '宋体' if raw_font == bytes.fromhex('cbcecce5') else 'non-ASCII font bytes ' + raw_font.hex()
                objects[handle] = (name, payload[13])
            else:
                objects[handle] = None
        elif function == 0x01F0:
            if len(payload) < 2:
                raise ValueError('WMF object deletion is truncated')
            handle, = struct.unpack_from('<H', payload)
            if handle not in objects:
                raise ValueError('WMF deletes an unknown object')
            objects.pop(handle)
        elif function == 0x012D:
            if len(payload) < 2:
                raise ValueError('WMF object selection is truncated')
            handle, = struct.unpack_from('<H', payload)
            if handle not in objects:
                raise ValueError('WMF selects an unknown/stock object')
            if objects[handle] is not None:
                font, charset = objects[handle]
        elif function in (0x0A32, 0x0521):
            if function == 0x0A32:
                if len(payload) < 8:
                    raise ValueError('WMF text record is truncated')
                count, options = struct.unpack_from('<HH', payload, 4)
                start = 8 + (8 if options & 6 else 0)
                if options & ~6:
                    raise ValueError('WMF text options are not verified')
            else:
                if len(payload) < 2:
                    raise ValueError('WMF text record is truncated')
                count, = struct.unpack_from('<H', payload)
                start = 2
            text = payload[start:start + count]
            if len(text) != count:
                raise ValueError('WMF text bytes are truncated')
            if count:
                if font == 'times new roman' and charset == 0:
                    if any(c < 32 or c > 126 for c in text):
                        raise ValueError('Unverified Latin glyph')
                    decoded = text.decode('ascii')
                elif font == 'symbol' and charset == 1:
                    mapping = {0x44: 'Δ', 0xAD: '↑'}
                    if any(c not in mapping for c in text):
                        raise ValueError('Unverified Symbol glyph')
                    decoded = ''.join(mapping[c] for c in text)
                elif font in ('宋体', 'simsun') and charset == 134:
                    # Windows charset134 / CP936 includes the GBK extension.
                    decoded = text.decode('gbk')
                    if not _cjk_covered(decoded):
                        raise ValueError('CJK glyph is absent from the pinned bundled font')
                else:
                    raise ValueError(f'Unverified WMF font/charset: {font}/{charset}')
                evidence.append({'font': font, 'charset': charset, 'bytes_hex': text.hex(), 'text': decoded})
                saw_drawing = True
        elif function == 0x0626:
            if len(payload) < 4:
                raise ValueError('WMF escape record is truncated')
            escape, count = struct.unpack_from('<HH', payload)
            if escape != 15 or count > len(payload) - 4:
                raise ValueError('WMF escape is not a verified metadata comment')
        elif function not in plain:
            raise ValueError(f'WMF record is not verified: 0x{function:04X}')
        if function == 0x0213:
            saw_drawing = True
        offset = end
    if not saw_end or not saw_drawing:
        raise ValueError('WMF is incomplete or has no supported visible content')

    return evidence


def runtime_paths() -> tuple[Path, Path, Path]:
    if sys.platform == 'win32':
        raise ValueError('Windows original-WMF LibreOffice font isolation is not yet validated')
    if getattr(sys, 'frozen', False):
        root = Path(sys._MEIPASS) / 'libreoffice' / 'LibreOfficeDev.app'
    else:
        value = os.environ.get('QA_PARSER_LIBREOFFICE_ROOT')
        if not value:
            raise ValueError('Optional bundled LibreOffice renderer is not available')
        root = Path(value)
    executable = root / 'Contents/MacOS/soffice'
    fonts = root / 'Contents/Resources/fonts/truetype'
    cjk = Path(__file__).parent / 'web/vendor/fonts/NotoSansCJKsc-Regular.otf'
    required = [executable, fonts / 'LiberationSerif-Regular.ttf', fonts / 'opens___.ttf', cjk]
    if not all(v.is_file() for v in required):
        raise ValueError('Optional LibreOffice runtime or explicit fonts are incomplete')
    return executable, fonts, cjk


def preview_to_png(data: bytes) -> dict:
    try:
        if not isinstance(data, bytes) or not data or len(data) > MAX_INPUT_BYTES:
            raise ValueError('WMF input is empty or exceeds size limit')
        evidence = audit_wmf(data)
        executable, fonts, cjk = runtime_paths()
        with tempfile.TemporaryDirectory(prefix='qa-lo-preview-') as directory:
            directory = Path(directory)
            config = directory / 'fonts.conf'
            config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                + ''.join(f'<dir>{escape(str(v.resolve()))}</dir>' for v in [fonts, cjk.parent])
                + f'<cachedir>{escape(str(directory / "cache"))}</cachedir>'
                + ''.join(f'<alias><family>{old}</family><prefer><family>{new}</family></prefer></alias>' for old, new in [('宋体','Noto Sans CJK SC'),('SimSun','Noto Sans CJK SC'),('Times New Roman','Liberation Serif'),('Symbol','OpenSymbol')])
                + '</fontconfig>', encoding='utf-8')
            source = directory / 'source.wmf'
            source.write_bytes(data)
            command = [str(executable), '-env:UserInstallation=' + (directory / 'profile').as_uri(), '--headless', '--convert-to',
                'png:draw_png_Export:{"PixelWidth":{"type":"long","value":"2448"},"PixelHeight":{"type":"long","value":"3168"}}',
                '--outdir', str(directory), str(source)]
            env = dict(os.environ, FONTCONFIG_FILE=str(config), FONTCONFIG_PATH=str(directory))
            process = subprocess.run(command, capture_output=True, timeout=30, check=False, env=env)
            target = directory / 'source.png'
            if process.returncode or not target.is_file() or target.stat().st_size > MAX_INPUT_BYTES:
                raise ValueError('LibreOffice failed to produce bounded PNG output')
            result = _validate_png(target.read_bytes(), ENGINE)
            from PIL import Image, ImageChops
            with Image.open(io.BytesIO(result['png'])) as image:
                bounds = ImageChops.difference(image, Image.new('RGB', image.size, 'white')).getbbox()
                if bounds is None:
                    raise ValueError('LibreOffice produced blank output')
                left, top, right, bottom = bounds
                cropped = image.crop((max(0, left-4), max(0, top-4), min(image.width, right+4), min(image.height, bottom+4)))
                output = io.BytesIO()
                cropped.save(output, format='PNG')
            result = _validate_png(output.getvalue(), ENGINE)
            result['font_evidence'] = evidence
            return result
    except (OSError, ValueError, struct.error, subprocess.TimeoutExpired) as error:
        return _failed(str(error), ENGINE)
