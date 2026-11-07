"""Resolve a narrow MTEF explicit-font subset without raster font guessing.

The MTEF v5 CHAR MTCode identifies a character independently of its font;
FONT_STYLE_DEF and EQN_PREFS retain the font/style needed to avoid treating
vector, blackboard, or arbitrary user styles as plain Latin characters.

Specification (original Design Science documentation, preserved copy):
https://rtf2latex2e.sourceforge.net/MTEF5.html
Current vendor font knowledge reference:
https://docs.wiris.com/reference/font-knowledge

No WMF renderer/font gate is modified here. Unknown bindings remain unknown.
"""
from __future__ import annotations

from .vendor import mtef


class FontMetadataMixin:
    """Retain font/style definitions discarded by the small upstream parser.

    Inherit before MTEFParser. The existing binary readers remain authoritative;
    this only captures the two definition records and associates each CHAR with
    the definition present at the point it was read.
    """

    def __init__(self, body: bytes):
        super().__init__(body)
        self.explicit_font_styles: list[tuple[int, int]] = []
        self.logical_font_styles: list[tuple[int, int | None]] = []

    def read_record(self, top=False):
        if self.r.peek() == mtef.FONT_STYLE_DEF:
            self.r.u8()
            self.explicit_font_styles.append((self.r.unsigned_int(), self.r.u8()))
            return None
        return super().read_record(top=top)

    def read_eqn_prefs(self):
        self.read_dim_array()
        self.read_dim_array()
        self.styles = []
        self.logical_font_styles = []
        for _ in range(self.r.u8()):
            index = self.r.unsigned_int()
            style = self.r.u8() if index else None
            self.styles.append(index)
            self.logical_font_styles.append((index, style))

    def read_char(self, opt):
        return self.bind_font(super().read_char(opt), opt)

    def bind_font(self, node, opt):
        """Attach the active binding to a CHAR read by any compatible adapter."""
        node.source_char_options = opt
        node.source_font = None
        tf = node.typeface
        definitions = self.explicit_font_styles if tf < 0 else self.logical_font_styles
        index = -tf - 1 if tf < 0 else tf - 1
        if 0 <= index < len(definitions):
            font_index, style = definitions[index]
            if 1 <= font_index <= len(self.font_defs):
                encoding, name = self.font_defs[font_index - 1]
                node.source_font = {'font_index': font_index, 'name': name,
                                    'encoding': encoding, 'style': style}
        return node


# Families and plain styles verified in the actual fixed input objects. The
# Unicode scalar is still required: this is not an encoded-byte-to-glyph map.
_PLAIN_FONTS = {
    b'Times New Roman', b'Arial', b'Courier New', b'Symbol', b'Euclid Symbol',
    b'Malgun Gothic', b'Batang', b'SimSun', b'\xcb\xce\xcc\xe5',
}
_SYMBOL_LATEX = {0x2191: r'\uparrow', 0x2193: r'\downarrow',
                 0x0394: r'\Delta', 0x2236: r'\mathrel{:}'}


def is_plain_unicode_custom_font(node: mtef.Text) -> bool:
    """True only for resolved plain font + a representable Unicode character.

    Vector style 7 remains rejected, as do unknown/user-alphabet fonts, bold or
    italic custom styles, PUA, absent MTCode, controls and inconsistent text.
    A finite symbol set and CJK/ASCII cover the seven audited source objects.
    """
    if not isinstance(node, mtef.Text):
        return False
    tf = node.typeface
    if not isinstance(tf, int) or not (tf < 0 or tf in (9, 10)):
        return False
    binding = getattr(node, 'source_font', None)
    if not isinstance(binding, dict) or binding.get('name') not in _PLAIN_FONTS or binding.get('style') != 0:
        return False
    cp = node.mtcode
    if not isinstance(cp, int) or not (0 < cp <= 0xFFFF) or node.s != chr(cp):
        return False
    if node.embell:
        return False
    return cp in _SYMBOL_LATEX or 0x20 <= cp <= 0x7E or 0x3400 <= cp <= 0x9FFF


def plain_custom_font_latex(node: mtef.Text) -> str | None:
    """Render only the subset above, preserving plain Latin and CJK text."""
    if not is_plain_unicode_custom_font(node):
        return None
    if node.mtcode in _SYMBOL_LATEX:
        return _SYMBOL_LATEX[node.mtcode]
    escaped = ''.join(mtef.LATEX_SPECIAL.get(c, c) for c in node.s)
    if 0x3400 <= node.mtcode <= 0x9FFF:
        return r'\text{' + escaped + '}'
    if node.s.isalpha() or node.s.isdigit():
        return r'\mathrm{' + escaped + '}'
    return escaped
