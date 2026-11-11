"""Loss-aware OOXML reader built on lxml and Python's ZIP implementation.

This module deliberately does not decide which paragraph belongs to a question.
Every substantive object is emitted once, with its original XML location.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from zipfile import ZipFile, BadZipFile
import posixpath
import re

from lxml import etree

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'v': 'urn:schemas-microsoft-com:vml',
    'o': 'urn:schemas-microsoft-com:office:office',
    'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
}
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_ENTRY = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_ENTRIES = 10000


def q(prefix: str, name: str) -> str:
    return f'{{{NS[prefix]}}}{name}'


def _xml(data: bytes):
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=False, remove_blank_text=False)
    root = etree.fromstring(data, parser=parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError('DOCX XML must not contain a document type declaration')
    return root


def _latex_text(value: str) -> str:
    escaped = {'\\': r'\backslash{}', '{': r'\{', '}': r'\}',
               '$': r'\$', '%': r'\%', '&': r'\&', '#': r'\#',
               '_': r'\_', '^': r'\textasciicircum{}', '~': r'\sim '}
    return ''.join(escaped.get(c, c) for c in value)


def _extension(data: bytes, target: str) -> str:
    """Use actual signatures, not a misleading relationship filename."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if data.startswith(b'BM'):
        return '.bmp'
    if data.startswith((b'II*\x00', b'MM\x00*')):
        return '.tiff'
    if data.startswith(b'\xd7\xcd\xc6\x9a') or data[:4] in (b'\x01\x00\x09\x00', b'\x02\x00\x09\x00'):
        return '.wmf'
    if len(data) >= 44 and data[40:44] == b' EMF':
        return '.emf'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    # Preserve bytes without claiming that unrecognized bytes are a valid image.
    return PurePosixPath(target).suffix.lower() or '.bin'


class _Reader:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.issues: list[dict] = []
        self.blocks: list[dict] = []
        self.root = _xml(archive.read('word/document.xml'))
        self.tree = self.root.getroottree()
        self.relationships = {}
        if 'word/_rels/document.xml.rels' in archive.namelist():
            rels = _xml(archive.read('word/_rels/document.xml.rels'))
            for rel in rels:
                rid = rel.get('Id')
                if rid in self.relationships:
                    raise ValueError(f'Duplicate relationship id: {rid}')
                self.relationships[rid] = dict(rel.attrib)

    def location(self, node) -> str:
        return 'word/document.xml:' + self.tree.getpath(node)

    def problem(self, node, reason: str, code: str = 'unsupported_content') -> dict:
        location = self.location(node)
        self.issues.append({'code': code, 'reason': reason, 'location': location,
                            'severity': 'review'})
        return {'kind': 'unsupported', 'reason': reason, 'location': location}

    def resource(self, node, rid: str | None):
        if not rid or rid not in self.relationships:
            return None, None, self.problem(node, f'Missing resource relationship: {rid}', 'missing_resource')
        rel = self.relationships[rid]
        if rel.get('TargetMode', '').lower() == 'external':
            return None, None, self.problem(node, f'External resource is not embedded: {rid}', 'external_resource')
        target = unquote(rel.get('Target', ''))
        if '\\' in target or ':' in target or '\x00' in target:
            return None, None, self.problem(node, f'Invalid resource path: {rid}', 'invalid_resource')
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('word', target))
        if path.startswith('../') or path in ('.', '..'):
            return None, None, self.problem(node, f'Unsafe resource path: {rid}', 'invalid_resource')
        try:
            return self.archive.read(path), path, None
        except KeyError:
            return None, None, self.problem(node, f'Missing resource part: {path}', 'missing_resource')

    def choose(self, node):
        # DrawingML, legacy VML and common Word namespaces are handled here.
        supported = set(NS.values()) | {
            'http://schemas.openxmlformats.org/drawingml/2006/picture',
            'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
        }
        for choice in node.findall(q('mc', 'Choice')):
            if all(choice.nsmap.get(p) in supported for p in choice.get('Requires', '').split()):
                return choice
        fallback = node.find(q('mc', 'Fallback'))
        return fallback

    def image(self, node):
        rid = node.get(q('r', 'embed')) or node.get(q('r', 'link')) or node.get(q('r', 'id'))
        data, target, error = self.resource(node, rid)
        if error:
            return error
        ext = _extension(data, target)
        token = {'kind': 'image', 'data': data, 'extension': ext,
                 'location': self.location(node), 'source_part': target}
        known = ('.png', '.jpeg', '.gif', '.bmp', '.tiff', '.wmf', '.emf', '.webp')
        if ext not in known or _extension(data, '') == '.bin':
            token['review_reason'] = 'Image encoding is not recognized; preserved original bytes'
            self.problem(node, token['review_reason'], 'image_format')
        return token

    def object(self, node):
        objects = node.findall('.//' + q('o', 'OLEObject'))
        if len(objects) != 1:
            return [self.problem(node, 'Embedded object does not contain exactly one OLE object')]
        obj = objects[0]
        prog = obj.get('ProgID', '')
        if prog not in ('Equation.DSMT4', 'Equation.3'):
            return [self.problem(obj, f'Unsupported OLE object type: {prog}')]
        data, target, error = self.resource(obj, obj.get(q('r', 'id')))
        if error:
            return [error]
        preview = None
        preview_ext = None
        errors = []
        shape_id = obj.get('ShapeID')
        shapes = node.findall('.//' + q('v', 'shape'))
        matching_shapes = [shape for shape in shapes if shape.get('id') == shape_id] if shape_id else shapes
        candidates = matching_shapes[0].findall('.//' + q('v', 'imagedata')) if len(matching_shapes) == 1 else []
        image_node = candidates[0] if len(candidates) == 1 else None
        binding = {'shape_id': shape_id, 'mode': 'shape_id' if shape_id else 'single_shape',
                   'verified': image_node is not None}
        if image_node is None:
            binding['reason'] = 'No unique preview image bound to this OLE shape'
        if image_node is not None:
            preview, preview_path, error = self.resource(image_node, image_node.get(q('r', 'id')))
            if error:
                errors.append(error)
            elif preview is not None:
                preview_ext = _extension(preview, preview_path)
                binding.update(source_part=preview_path, relationship_id=image_node.get(q('r', 'id')),
                               location=self.location(image_node))
        return [{'kind': 'formula', 'format': 'mathtype', 'data': data,
                 'preview': preview, 'preview_ext': preview_ext,
                 'preview_binding': binding,
                 'location': self.location(obj), 'source_part': target,
                 'source_xml': etree.tostring(node, encoding='unicode')}] + errors

    def inline(self, node):
        tag = node.tag
        if not isinstance(tag, str):
            return []
        if tag == q('mc', 'AlternateContent'):
            selected = self.choose(node)
            return self.children(selected) if selected is not None else [self.problem(node, 'No supported alternate content branch')]
        if tag == q('w', 'object'):
            return self.object(node)
        if tag == q('m', 'oMath'):
            return [{'kind': 'formula', 'format': 'omml', 'data': etree.tostring(node),
                     'preview': None, 'preview_ext': None, 'location': self.location(node)}]
        if tag in (q('a', 'blip'), q('v', 'imagedata')):
            return [self.image(node)]
        if tag == q('w', 't'):
            return [{'kind': 'text', 'text': node.text or '', 'location': self.location(node)}]
        if tag in (q('w', 'tab'), q('w', 'br'), q('w', 'cr')):
            return [{'kind': 'text', 'text': '\t' if tag == q('w', 'tab') else '\n', 'location': self.location(node)}]
        if tag == q('w', 'noBreakHyphen'):
            return [{'kind': 'text', 'text': '\u2011', 'location': self.location(node)}]
        if tag == q('w', 'softHyphen'):
            return [{'kind': 'text', 'text': '\u00ad', 'location': self.location(node)}]
        if tag == q('w', 'r'):
            result = []
            props = node.find(q('w', 'rPr'))
            align = props.find(q('w', 'vertAlign')) if props is not None else None
            underline = props.find(q('w', 'u')) if props is not None else None
            for child in node:
                if child.tag != q('w', 'rPr'):
                    result.extend(self.inline(child))
            if align is not None and align.get(q('w', 'val')) in ('subscript', 'superscript'):
                script = '_' if align.get(q('w', 'val')) == 'subscript' else '^'
                for token in result:
                    if token['kind'] == 'text' and token['text'].strip():
                        script_text = token.pop('text')
                        token.update(kind='formula', format='latex', data=('{}' + script + '{' + _latex_text(script_text) + '}').encode(),
                                     preview=None, preview_ext=None, origin='word_vertical_alignment',
                                     script=script, script_text=script_text)
            if underline is not None and underline.get(q('w', 'val'), 'single') != 'none':
                for token in result:
                    token['underline'] = True
            fonts = props.find(q('w', 'rFonts')) if props is not None else None
            if fonts is not None and any(
                any(label in str(value).lower() for label in ('symbol', 'wingdings', 'webdings', 'mt extra'))
                for value in fonts.attrib.values()
            ):
                result.insert(0, self.problem(node, 'Symbol font text requires glyph mapping review', 'symbol_font'))
            return result
        if tag == q('w', 'fldSimple'):
            return [self.problem(node, 'Word field code requires review; cached result preserved', 'field_code')] + self.children(node)
        if tag == q('w', 'instrText') or (tag == q('w', 'fldChar') and node.get(q('w', 'fldCharType')) == 'begin'):
            return [self.problem(node, 'Word field code requires review; cached result preserved', 'field_code')]
        if tag in (q('w', 'sym'), q('w', 'txbxContent'), q('w', 'altChunk'), q('w', 'subDoc')):
            return [self.problem(node, f'Unsupported content: {etree.QName(node).localname}')]
        if tag == q('w', 'del'):
            return [self.problem(node, 'Tracked deletion requires source review')]
        if tag in (q('w', 'footnoteReference'), q('w', 'endnoteReference')):
            return [self.problem(node, 'Footnote/endnote content requires source review')]
        # Ignore metadata, never recurse into drawing properties or cached text.
        local = etree.QName(node).localname
        if local.endswith('Pr') or local in {'bookmarkStart', 'bookmarkEnd', 'proofErr', 'permStart', 'permEnd',
                                            'commentRangeStart', 'commentRangeEnd', 'commentReference', 'lastRenderedPageBreak',
                                            'fldChar'}:
            return []
        containers = {'p', 'hyperlink', 'smartTag', 'sdt', 'sdtContent', 'ins', 'moveTo',
                      'oMathPara', 'drawing', 'pict', 'inline', 'anchor', 'graphic', 'graphicData',
                      'pic', 'blipFill', 'shape', 'group', 'Choice', 'Fallback', 'fldSimple'}
        if local in containers:
            return self.children(node)
        drawing_meta = {'extent', 'effectExtent', 'docPr', 'cNvGraphicFramePr', 'positionH', 'positionV',
                        'simplePos', 'wrapNone', 'wrapSquare', 'wrapTight', 'wrapThrough', 'wrapTopAndBottom',
                        'nvPicPr', 'stretch', 'tile', 'shapetype', 'stroke', 'fill', 'path', 'lock', 'formulas'}
        if local in drawing_meta:
            return []
        if local == 'srcRect':
            return [self.problem(node, 'Image crop is preserved in source XML but not applied to original image', 'image_transform')]
        return [self.problem(node, f'Unsupported XML element: {local}')]

    def children(self, node):
        result = []
        for child in node:
            result.extend(self.inline(child))
        return result

    def normalize_group_scripts(self, tokens):
        """Keep explicitly parenthesized Word run groups together, e.g. (NO₃)₂.

        w:vertAlign positions the run, not a semantic formula base. We accept
        only contiguous alphanumeric groups with matching original brackets;
        prose and embedded objects are hard boundaries. Spaces immediately inside
        an opening bracket and trailing spaces in a group script remain source
        layout; spaces between a closing bracket and its script still break binding.
        """
        output, pending = [], []
        pairs = {')': '(', '）': '（'}

        def flush():
            if not pending:
                return
            # Keep source small-question labels outside the formula.
            if pending[0]['kind'] == 'text':
                label = re.match(r'^(?:\([0-9]+\)|（[0-9]+）)(?=[A-Za-z])', pending[0]['text'])
                if label:
                    output.append({**pending[0], 'text': label.group()})
                    pending[0] = {**pending[0], 'text': pending[0]['text'][label.end():]}
            stack, parts, last, grouped, valid = [], [], '', False, True
            closed_scripted_group = False
            scripts = set()
            for token in pending:
                if token['kind'] == 'text':
                    value = token['text']
                    for char in value:
                        if char in '(（':
                            stack.append([char, False])
                            if len(stack) > 32:
                                valid = False
                        elif char in ')）':
                            if not stack or stack[-1][0] != pairs[char] or last in '(（':
                                valid = False
                            else:
                                closed_scripted_group = stack.pop()[1]
                        last = char
                    parts.append({'text': value})
                    scripts.clear()
                else:
                    script = token['script']
                    value = token['script_text']
                    checked_value = value.rstrip(' \t') if last in ')）' else value
                    if not last or last in '(（' or not re.fullmatch(r'[A-Za-z0-9+-]+', checked_value):
                        valid = False
                    if script in scripts:
                        if parts and parts[-1].get('script') == script:
                            parts[-1]['text'] += value
                            continue
                        valid = False
                    scripts.add(script)
                    grouped |= last in ')）' and closed_scripted_group
                    for entry in stack:
                        entry[1] = True
                    parts.append({'script': script, 'text': value})
            if valid and grouped and not stack:
                data = ''.join(
                    part['script'] + '{' + _latex_text(part['text']) + '}'
                    if 'script' in part else _latex_text(part['text']).replace('（', '(').replace('）', ')')
                    for part in parts).encode()
                output.append({'kind': 'formula', 'format': 'latex', 'data': data,
                               'preview': None, 'preview_ext': None,
                               'origin': 'word_group_script_sequence',
                               'location': pending[0]['location'],
                               'source_locations': list(dict.fromkeys(t['location'] for t in pending)),
                               'word_segments': parts,
                               **({'underline': True} if any(t.get('underline') for t in pending) else {})})
            else:
                output.extend(pending)
            pending.clear()

        for token in tokens:
            if token['kind'] == 'text':
                for chunk in re.findall(r'[A-Za-z0-9()（）]+|[^A-Za-z0-9()（）]+', token['text']):
                    item = {**token, 'text': chunk}
                    if re.fullmatch(r'[A-Za-z0-9()（）]+', chunk):
                        pending.append(item)
                    elif (pending and pending[-1]['kind'] == 'text'
                          and pending[-1]['text'].endswith(('(', '（'))
                          and re.fullmatch(r'[ \t]+', chunk)):
                        pending.append(item)
                    else:
                        flush()
                        output.append(item)
            elif token.get('origin') == 'word_vertical_alignment':
                pending.append(token)
                if token['script_text'] != token['script_text'].rstrip(' \t'):
                    flush()
            else:
                flush()
                output.append(token)
        flush()
        # Restore unaffected text-run boundaries after candidate tokenization.
        merged = []
        for token in output:
            if (merged and token['kind'] == 'text' and merged[-1]['kind'] == 'text'
                    and {k: v for k, v in token.items() if k != 'text'}
                    == {k: v for k, v in merged[-1].items() if k != 'text'}):
                merged[-1]['text'] += token['text']
            else:
                merged.append(token)
        return merged

    def normalize_scripts(self, tokens):
        # Word stores scripts as separate runs. Attach only to explicit adjacent
        # ASCII math/chemical text, never to an invented empty math base. Other
        # bases retain their source and are marked for review.
        tokens = self.normalize_group_scripts(tokens)
        result = []
        base_pattern = re.compile(r'(?:[A-Za-z0-9]+(?:\.[0-9]+)?|\([A-Za-z0-9]+\)|（[A-Za-z0-9]+）)+$')
        continuation_pattern = re.compile(r'^[A-Za-z0-9]+')

        def render(token):
            pieces = token['word_segments']
            token['data'] = ''.join(
                _latex_text(part['text']).replace('（', '(').replace('）', ')')
                if 'script' not in part else part['script'] + '{' + _latex_text(part['text']) + '}'
                for part in pieces
            ).encode('utf-8')

        def active(token):
            return token.get('origin') == 'word_script_sequence'

        for token in tokens:
            if token.get('origin') == 'word_vertical_alignment':
                previous = result[-1] if result else None
                if previous is not None and previous['kind'] == 'text':
                    match = base_pattern.search(previous['text'])
                    if match:
                        base = match.group()
                        previous['text'] = previous['text'][:match.start()]
                        source_location = previous['location']
                        if not previous['text']:
                            result.pop()
                        previous = {
                            'kind': 'formula', 'format': 'latex', 'data': b'',
                            'preview': None, 'preview_ext': None,
                            'origin': 'word_script_sequence', 'location': source_location,
                            'source_locations': [source_location],
                            'word_segments': [{'text': base}],
                        }
                        result.append(previous)
                if previous is not None and active(previous):
                    part = {'script': token['script'], 'text': token['script_text']}
                    if previous['word_segments'][-1].get('script') == token['script']:
                        previous['word_segments'][-1]['text'] += part['text']
                    elif any(p.get('script') == token['script'] for p in previous['word_segments'][-2:]
                             if 'script' in p) and 'script' in previous['word_segments'][-1]:
                        # Repeating a subscript around a superscript is ambiguous.
                        token['review_reason'] = 'Repeated non-adjacent Word script requires review'
                        self.issues.append({'code': 'word_script', 'reason': token['review_reason'],
                                            'location': token['location'], 'severity': 'review'})
                        result.append(token)
                        continue
                    else:
                        previous['word_segments'].append(part)
                    previous['source_locations'].append(token['location'])
                    render(previous)
                    continue
                token['review_reason'] = 'Word script has no reliably identifiable adjacent base'
                self.issues.append({'code': 'word_script', 'reason': token['review_reason'],
                                    'location': token['location'], 'severity': 'review'})
            elif token['kind'] == 'text' and result and active(result[-1]):
                # Keep e.g. H₂O and Fe₂O₃ in a single formula, but stop at
                # whitespace, punctuation, CJK prose or an embedded object.
                match = continuation_pattern.match(token['text'])
                if match:
                    result[-1]['word_segments'].append({'text': match.group()})
                    result[-1]['source_locations'].append(token['location'])
                    render(result[-1])
                    token['text'] = token['text'][match.end():]
                    if not token['text']:
                        continue
            result.append(token)
        return result

    def paragraph(self, node):
        tokens = self.normalize_scripts(self.children(node))
        if node.find('./' + q('w', 'pPr') + '/' + q('w', 'numPr')) is not None:
            tokens.insert(0, self.problem(node, 'Automatic paragraph numbering requires review', 'automatic_numbering'))
        return tokens

    def add(self, node, kind, tokens, **metadata):
        self.blocks.append({'index': len(self.blocks), 'location': self.location(node),
                            'kind': kind, 'tokens': tokens,
                            'source_xml': etree.tostring(node, encoding='unicode'), **metadata})

    def table(self, table):
        for row_index, row in enumerate(table.findall(q('w', 'tr'))):
            tokens = []
            cells = []
            for cell_index, cell in enumerate(row.findall(q('w', 'tc'))):
                if cell_index:
                    tokens.append({'kind': 'text', 'text': '\t', 'location': self.location(cell)})
                start = len(tokens)
                paragraphs = cell.findall(q('w', 'p'))
                for p_index, paragraph in enumerate(paragraphs):
                    if p_index:
                        tokens.append({'kind': 'text', 'text': '\n', 'location': self.location(paragraph)})
                    tokens.extend(self.paragraph(paragraph))
                for nested in cell:
                    if nested.tag not in (q('w', 'p'), q('w', 'tcPr')):
                        tokens.append(self.problem(nested, 'Nested table or unsupported cell content requires review'))
                cells.append({'index': cell_index, 'location': self.location(cell),
                              'token_start': start, 'token_end': len(tokens),
                              'properties_xml': etree.tostring(cell.find(q('w', 'tcPr')), encoding='unicode')
                              if cell.find(q('w', 'tcPr')) is not None else None})
            self.add(row, 'table_row', tokens, table_location=self.location(table), row=row_index, cells=cells)

    def body(self, node):
        for child in node:
            if child.tag == q('w', 'p'):
                self.add(child, 'paragraph', self.paragraph(child))
            elif child.tag == q('w', 'tbl'):
                self.table(child)
            elif child.tag in (q('w', 'sdt'), q('w', 'sdtContent'), q('w', 'ins')):
                self.body(child)
            elif child.tag not in (q('w', 'sectPr'), q('w', 'sdtPr'), q('w', 'sdtEndPr')):
                self.add(child, 'paragraph', [self.problem(child, f'Unsupported body element: {etree.QName(child).localname}')])


def read_document(path: Path) -> dict:
    """Read DOCX into ordered blocks and loss-aware inline tokens.

    Raises ValueError for a malformed/unsafe document; localized unsupported
    content is returned as review tokens and issues instead of being dropped.
    Assets remain bytes in memory; the caller owns persistence and numbering.
    """
    path = Path(path)
    if path.suffix.lower() != '.docx':
        raise ValueError('Only .docx input is supported')
    if path.stat().st_size > MAX_ARCHIVE:
        raise ValueError('DOCX exceeds compressed size limit')
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_TOTAL:
                raise ValueError('DOCX exceeds archive limits')
            names = [e.filename for e in entries]
            if len(set(names)) != len(names):
                raise ValueError('Duplicate DOCX archive entries')
            for entry in entries:
                parts = PurePosixPath(entry.filename).parts
                if entry.file_size > MAX_ENTRY or entry.filename.startswith('/') or '..' in parts or '\\' in entry.filename:
                    raise ValueError('DOCX contains oversized or unsafe archive entry')
            if 'word/document.xml' not in names:
                raise ValueError('DOCX does not contain word/document.xml')
            reader = _Reader(archive)
            body = reader.root.find(q('w', 'body'))
            if body is None:
                raise ValueError('DOCX has no document body')
            reader.body(body)
            return {'blocks': reader.blocks, 'issues': reader.issues}
    except (BadZipFile, etree.XMLSyntaxError, RuntimeError) as exc:
        raise ValueError(f'Invalid DOCX: {exc}') from exc
