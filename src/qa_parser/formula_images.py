"""Conservative conversion of *original* formula previews into PNG.

Raster decoding/encoding is delegated to Pillow; WMF rendering is delegated to
libwmf's wmf2gd executable. A metadata-only audit rejects WMF font/record cases
which the available renderer can silently corrupt. This is not a WMF renderer.
No conversion from guessed LaTeX and no network access occurs here.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 8192
WMF_ENGINE = 'libwmf/wmf2gd+ascii-font-audit/2'
RASTER_ENGINE = 'Pillow/original-preview/1'


def _wmf_runtime(directory: Path) -> tuple[str | None, list[str]]:
    """Resolve the frozen renderer and generate its relocatable font map.

    Frozen builds deliberately never fall through to developer Homebrew/PATH.
    Font map files live in the temporary conversion directory, so a read-only
    application bundle remains usable and moving the bundle is safe.
    """
    if getattr(sys, 'frozen', False):
        root = Path(sys._MEIPASS) / 'wmf'
        executable = root / 'bin' / ('wmf2gd.exe' if sys.platform == 'win32' else 'wmf2gd')
        fonts = root / 'fonts'
        if not executable.is_file() or not (fonts / 'fontmap').is_file():
            return None, []
        # libwmf's narrow Windows stat/fopen cannot resolve UTF-8 absolute
        # paths in its fontmap. Python copies via wide paths; the child uses
        # ASCII paths relative to its wide-character working directory.
        relative = sys.platform == 'win32'
        if relative:
            shutil.copytree(fonts, directory / 'fonts')
            fonts = directory / 'fonts'
        tree = ET.parse(fonts / 'fontmap')
        for font in tree.getroot():
            for key in ('metrics', 'glyphs'):
                name = Path(font.attrib[key]).name
                path = fonts / name
                if not path.is_file():
                    raise ValueError(f'Bundled WMF font is missing: {name}')
                font.attrib[key] = path.relative_to(directory).as_posix() if relative else str(path)
        fontmap = directory / 'fontmap.xml'
        tree.write(fontmap, encoding='utf-8', xml_declaration=True)
        empty_gs = directory / 'Fontmap.GS'
        empty_gs.write_text('', encoding='ascii')
        return str(executable), [f'--wmf-fontdir={"fonts" if relative else fonts}', '--wmf-sys-fonts',
            f'--wmf-sys-fontmap={fontmap.name if relative else fontmap}', '--wmf-xtra-fonts',
            f'--wmf-xtra-fontmap={fontmap.name if relative else fontmap}', f'--wmf-gs-fontmap={empty_gs.name if relative else empty_gs}']
    return os.environ.get('QA_PARSER_WMF2GD') or shutil.which('wmf2gd'), []
# Font substitution between these Latin families changes appearance, not ASCII
# semantics. Symbol, MT Extra, CJK and arbitrary custom fonts are NOT accepted.
LATIN_FONTS = {'times new roman', 'times', 'times-roman', 'arial', 'helvetica',
               'courier new', 'courier', 'system'}


def _failed(reason: str, engine: str) -> dict:
    return {'status': 'failed', 'png': None, 'reason': reason,
            'engine': engine, 'width': 0, 'height': 0}


def _audit_wmf(data: bytes) -> None:
    """Bounded record/font audit; reject unsupported semantics before rendering.

    In real MathType samples libwmf exits zero even after dropping CJK text,
    Greek glyphs and arrows. Only observed/supported drawing records and ASCII
    text in explicit Latin fonts are permitted by this adapter.
    """
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
                # An unrecognized font is retained as a rejected sentinel and
                # only blocks if actually selected for text, not merely defined.
                try:
                    name = raw_font.decode('ascii').strip().lower()
                except UnicodeDecodeError:
                    name = 'non-ASCII font bytes ' + raw_font.hex()
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
                if font not in LATIN_FONTS:
                    raise ValueError(f'WMF uses an unverified font for text: {font}')
                if charset not in (0, 1):
                    raise ValueError(f'WMF uses an unverified font charset: {charset}')
                if any(c < 32 or c > 126 for c in text):
                    raise ValueError('WMF non-ASCII glyph mapping is not verified')
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


def _validate_png(data: bytes, engine: str) -> dict:
    from PIL import Image, ImageChops
    with Image.open(io.BytesIO(data)) as original:
        if original.format != 'PNG':
            raise ValueError('Renderer did not produce PNG bytes')
        width, height = original.size
        if not width or not height or max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
            raise ValueError('Preview dimensions exceed supported bounds')
        original.load()
        rgba = original.convert('RGBA')
    # Decode and examine visible pixels, not just the PNG signature/exit status.
    image = Image.new('RGBA', rgba.size, 'white')
    image.alpha_composite(rgba)
    image = image.convert('RGB')
    if ImageChops.difference(image, Image.new('RGB', image.size, 'white')).getbbox() is None:
        raise ValueError('Converted formula preview is blank')
    if len(image.getcolors(maxcolors=2) or []) == 1:
        raise ValueError('Converted formula preview has no distinguishable content')
    output = io.BytesIO()
    image.save(output, format='PNG')
    return {'status': 'converted', 'png': output.getvalue(), 'reason': '',
            'engine': engine, 'width': width, 'height': height}


def _wmf_png_bytes(data: bytes) -> bytes:
    # The pinned MSYS2 libwmf 0.2.16 opens output with fopen(..., "w").
    # Windows inserts CR before each LF, including PNG's binary signature.
    # Reverse only that recognizable transport encoding, never image content;
    # require exact round-trip and all PNG chunk CRCs before accepting it.
    if sys.platform == 'win32' and data.startswith(b'\x89PNG\r\r\n\x1a\r\n'):
        restored = data.replace(b'\r\n', b'\n')
        if restored.replace(b'\n', b'\r\n') != data:
            raise ValueError('WMF PNG text-mode encoding is inconsistent')
        from PIL import Image
        with Image.open(io.BytesIO(restored)) as check:
            check.verify()
        return restored
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError(f'WMF renderer output is not PNG (header={data[:16].hex()})')
    return data


def preview_to_png(data: bytes, extension: str) -> dict:
    """Render the original embedded preview, or fail without guessing content.

    `converted` records a successful conservative conversion, not universal
    fidelity certification. The caller must retain source bytes/hash/location.
    WMF requires a separately provisioned wmf2gd, optionally selected with
    QA_PARSER_WMF2GD. Missing tools are failures, never runtime downloads.
    """
    ext = extension.lower().lstrip('.')
    engine = WMF_ENGINE if ext == 'wmf' else RASTER_ENGINE
    try:
        if not isinstance(data, bytes) or not data or len(data) > MAX_INPUT_BYTES:
            raise ValueError('Formula preview is empty or exceeds size limit')
        if ext == 'wmf':
            _audit_wmf(data)
            with tempfile.TemporaryDirectory(prefix='qa-formula-preview-') as directory:
                executable, font_options = _wmf_runtime(Path(directory))
                if not executable:
                    raise ValueError('wmf2gd is not available; original WMF is preserved')
                source = Path(directory) / 'source.wmf'
                target = Path(directory) / 'preview.png'
                source.write_bytes(data)
                windows = sys.platform == 'win32'
                command = [executable, *font_options, '--maxwidth=1600', '--maxheight=300', '--maxpect',
                           '--wmf-ignore-nonfatal=no', '-o', target.name if windows else str(target),
                           source.name if windows else str(source)]
                process = subprocess.run(command, capture_output=True, timeout=15, check=False,
                    **({'creationflags': subprocess.CREATE_NO_WINDOW, 'cwd': directory} if windows else {}))
                stderr = process.stderr.decode('utf-8', errors='replace')
                diagnostic = re.sub(r'[\d%\s\x08]+', '', stderr)
                if process.returncode or diagnostic:
                    raise ValueError(f'WMF renderer reported a problem: {stderr[-400:]}')
                if not target.is_file() or target.stat().st_size > MAX_INPUT_BYTES:
                    raise ValueError('WMF renderer output is missing or too large')
                return _validate_png(_wmf_png_bytes(target.read_bytes()), engine)
        if ext not in {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tif', 'tiff', 'webp'}:
            raise ValueError(f'Unsupported original preview format: {ext}')
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            expected = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'gif': 'GIF',
                        'bmp': 'BMP', 'tif': 'TIFF', 'tiff': 'TIFF', 'webp': 'WEBP'}[ext]
            if image.format != expected:
                raise ValueError('Original preview extension does not match image bytes')
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Multi-frame previews require explicit frame handling')
            width, height = image.size
            if max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise ValueError('Original preview dimensions exceed supported bounds')
            image.load()
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG')
        return _validate_png(output.getvalue(), engine)
    except Exception as exc:
        if ext == 'wmf':
            from .libreoffice_preview import preview_to_png as replay_original
            replay = replay_original(data)
            if replay['status'] == 'converted':
                return replay
        return _failed(f'{type(exc).__name__}: {exc}', engine)
