"""Small source-structure adapters; never use cached TeX as equation content.

Format reference: Design Science MTEF5 (1999), archived at
https://rtf2latex2e.sourceforge.net/MTEF5.html, CHAR/EMBELL, PILE/MATRIX,
HatBoxClass and FUTURE records. Keep unsupported structures fail-closed.
"""
from __future__ import annotations
from .vendor import mtef

# MathType spacing MTCode values, gated by fnSPACE (24). Reference:
# https://github.com/jure/mathtype_to_mathml/blob/master/lib/xsl/char.xsl
# These are spacing, not glyphs; ordinary LaTeX spacing normalizes font metrics.
SPACE_LATEX = {0xEF01: r'\hspace{0pt}', 0xEF02: r'\,',
               0xEF04: r'\;', 0xEF08: r'\hspace{1pt}'}


def read_char(parser, options):
    """Read CHAR and its actual EMBELL records, not COLOR indices as accents.

    The upstream loop treats every record in the embellishment object list as
    an EMBELL record. Real source lists contain COLOR (15), whose operand zero
    was wrongly appended as an unknown embellishment. Only black/default color
    metadata is accepted here; unknown semantic records still stop conversion.
    """
    if options & mtef.OPT_CHAR_ENC_CHAR_8 and options & mtef.OPT_CHAR_ENC_CHAR_16:
        raise mtef.MTEFError('CHAR 同时指定 8 位和 16 位字体位置')
    r = parser.r
    typeface = r.signed_int()
    mtcode = None if options & mtef.OPT_CHAR_ENC_NO_MTCODE else r.u16()
    font_position = r.u8() if options & mtef.OPT_CHAR_ENC_CHAR_8 else (
        r.u16() if options & mtef.OPT_CHAR_ENC_CHAR_16 else None)
    char = chr(mtcode) if mtcode else chr(font_position) if font_position is not None else ''
    embellishments = []
    if options & mtef.OPT_CHAR_EMBELL:
        while True:
            record = r.u8()
            if record == mtef.END:
                break
            if record == mtef.COLOR:
                color_index = r.unsigned_int()
                if color_index != 0:
                    raise mtef.MTEFError('装饰列表含非默认颜色，需核对颜色语义')
                continue
            if record != mtef.EMBELL:
                raise mtef.MTEFError(f'装饰列表中未支持的记录: {record}')
            embell_options = r.u8()
            if embell_options & ~mtef.OPT_NUDGE:
                raise mtef.MTEFError('装饰记录选项未支持')
            if embell_options & mtef.OPT_NUDGE:
                r.nudge()
            embellishments.append(r.u8())
    node = mtef.Text(char, typeface=typeface, embell=embellishments, mtcode=mtcode)
    node.source_font_position = font_position
    return node


def is_tex_input_metadata(record, payload):
    """Recognize exact named input-history envelope; content is never executed.

    MathType embeds this source-history extension as a null-terminated name and
    null-terminated byte string. It is ancillary to the full MTEF structural
    tree; ignoring stale history is safer than preferring it to edited content.
    Unknown extensions or malformed envelopes remain review items.
    """
    prefix = b'TeX Input Language\0'
    return (record == 102 and payload.startswith(prefix)
            and payload.endswith(b'\0')
            and b'\0' not in payload[len(prefix):-1])


def _vector_valid(node):
    if not isinstance(node, mtef.Tmpl) or node.selector != 31:
        return False
    # Single-headed full arrows; don't coerce harpoons or ambiguous directions.
    if node.variation not in (1, 2, 5, 6) or getattr(node, 'source_options', None) != 0:
        return False
    if len(node.slots) != 2 or not isinstance(node.slots[0], mtef.Line):
        return False
    glyph = node.slots[1]
    expected = 0x20D6 if node.variation & 1 else 0x20D7
    return (isinstance(glyph, mtef.Text) and glyph.mtcode == expected
            and glyph.typeface == 22 and not glyph.embell)


def _array_valid(node):
    if isinstance(node, mtef.Mtx):
        align = getattr(node, 'source_alignment', ())
        parts = getattr(node, 'source_partitions', b'')
        expected_parts = ((node.rows + 1) * 2 + 7) // 8 + ((node.cols + 1) * 2 + 7) // 8
        return (1 <= node.rows <= 32 and 1 <= node.cols <= 16
                and len(node.cells) == node.rows * node.cols
                and all(isinstance(c, (mtef.Line, mtef.Pile)) for c in node.cells)
                and align in ((1, 1, 1), (1, 2, 1), (1, 3, 1))
                and len(parts) == expected_parts and not any(parts))
    if isinstance(node, mtef.Pile):
        return (node.halign in (1, 2, 3) and node.valign == 1
                and getattr(node, 'source_options', None) == 0
                and 1 <= len(node.lines) <= 32
                and all(isinstance(c, mtef.Line) for c in node.lines))
    return False


def _limits_valid(node):
    # The maintained MTEF5 parser corrects old SDK documentation's low bits:
    # https://github.com/sbulka/mathtype/blob/master/lib/records5/tmpl.rb
    # 0x10 = lower, 0x20 = upper; LimBox order is main, lower, upper.
    if node.variation not in (0x10, 0x20, 0x30) or getattr(node, 'source_options', None) != 0:
        return False
    if len(node.slots) != 3 or not all(isinstance(s, mtef.Line) for s in node.slots):
        return False
    if not node.slots[0].items:
        return False
    return all(bool(node.variation & flag) or not slot.items
               for flag, slot in zip((0x10, 0x20), node.slots[1:]))


def audit_node(node, reasons):
    """True means this node family was handled, including invalid variations."""
    if isinstance(node, mtef.Text) and node.mtcode in SPACE_LATEX and node.typeface == 24:
        if node.embell:
            reasons.append('MTEF 间距字符带有装饰，不能当普通间距处理')
        return True
    if isinstance(node, mtef.Line):
        for index, child in enumerate(node.items):
            if isinstance(child, mtef.Tmpl) and child.selector == 28 and child.variation == 1:
                following = node.items[index + 1] if index + 1 < len(node.items) else None
                child.source_precedes_valid = (isinstance(following, mtef.Text)
                    and following.mtcode is not None and following.s.isalnum())
        return False
    if isinstance(node, mtef.Tmpl) and node.selector == 28 and node.variation == 1:
        if not (getattr(node, 'source_options', None) == 0
                and getattr(node, 'source_precedes_valid', False)
                and len(node.slots) == 2
                and all(isinstance(s, mtef.Line) for s in node.slots)
                and not node.slots[0].items and node.slots[1].items):
            reasons.append('MTEF 前置上标的后续基底或槽位不明确')
        return True
    if isinstance(node, mtef.Tmpl) and node.selector == 23:
        if not _limits_valid(node):
            reasons.append('MTEF 上下限模板标记或槽位不匹配')
        return True
    if isinstance(node, mtef.Tmpl) and node.selector == 31:
        if not _vector_valid(node):
            reasons.append('MTEF 向量模板方向、字符或槽位结构不匹配')
        return True
    if isinstance(node, (mtef.Mtx, mtef.Pile)):
        if not _array_valid(node):
            if isinstance(node, mtef.Mtx):
                metadata = f'alignment={getattr(node, "source_alignment", None)}, rows={node.rows}, cols={node.cols}, partitions={getattr(node, "source_partitions", b"").hex()}'
            else:
                metadata = f'halign={node.halign}, valign={node.valign}, options={getattr(node, "source_options", None)}'
            reasons.append('MTEF 多行/矩阵对齐、分隔线或槽位结构未支持 (' + metadata + ')')
        return True
    return False


def render_node(node):
    """Render already-audited node after recursive child adaptation."""
    if isinstance(node, mtef.Text) and node.mtcode in SPACE_LATEX and node.typeface == 24:
        return SPACE_LATEX[node.mtcode]
    if isinstance(node, mtef.Tmpl) and node.selector == 28 and node.variation == 1:
        return '{}^{' + mtef.to_latex(node.slots[1]) + '}'
    if isinstance(node, mtef.Tmpl) and node.selector == 23:
        main, lower, upper = map(mtef.to_latex, node.slots)
        body = main
        if lower:
            body = r'\underset{' + lower + '}{' + body + '}'
        if upper:
            body = r'\overset{' + upper + '}{' + body + '}'
        return body
    if isinstance(node, mtef.Tmpl) and node.selector == 31:
        command = ('under' if node.variation & 4 else 'over') + ('leftarrow' if node.variation & 1 else 'rightarrow')
        return '\\' + command + '{' + mtef.to_latex(node.slots[0]) + '}'
    if isinstance(node, mtef.Mtx):
        horizontal = {1: 'l', 2: 'c', 3: 'r'}[node.source_alignment[1]]
        rows = [node.cells[i:i + node.cols] for i in range(0, len(node.cells), node.cols)]
    elif isinstance(node, mtef.Pile):
        horizontal = {1: 'l', 2: 'c', 3: 'r'}[node.halign]
        rows = [[line] for line in node.lines]
    else:
        return None
    # Keep every empty row and cell; never infer reaction signs from rules/bars.
    body = r' \\ '.join(' & '.join(mtef.to_latex(cell) or '{}' for cell in row) for row in rows)
    return r'\begin{array}{' + horizontal * len(rows[0]) + '}' + body + r'\end{array}'
