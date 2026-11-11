"""Conservative adapters around reusable formula converters.

`converted` means accepted by the structural adapter, not human verified and not
proof that the destination application's renderer supports the emitted LaTeX.
The caller retains the complete original OMML/OLE object and source location.
"""
from __future__ import annotations

import io
import copy
import re
import xml.etree.ElementTree as ET
from typing import Any

from .vendor import mtef
from . import mtef_extensions
from .mtef_fonts import FontMetadataMixin, is_plain_unicode_custom_font, plain_custom_font_latex

MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
OMML_ENGINE = 'omml2latex/0.1.1+strict-adapter/2'
MTEF_ENGINE = 'formula-flow/mtef@9d5737a+strict-adapter/3'
MAX_FORMULA_BYTES = 2 * 1024 * 1024


def _result(latex: str | None, reasons: list[str], engine: str) -> dict[str, Any]:
    reasons = list(dict.fromkeys(reasons))
    if not latex and not reasons:
        reasons.append('公式转换结果为空')
    return {'latex': latex or None, 'status': 'review' if reasons else 'converted',
            'reason': '; '.join(reasons), 'engine': engine}


# This deliberately supports a smaller set than the dependency. Unknown or
# unverified constructs must not reach that library's silent empty fallback.
_OMML_SUPPORTED = set('''oMath oMathPara oMathParaPr jc r t rPr nor lit sty scr
f fPr type num den sSub sSubPr sub sSup sSupPr sup sSubSup sSubSupPr e
rad radPr deg degHide d dPr begChr endChr sepChr grow shp
limLow limLowPr limUpp limUppPr lim argPr argSz ctrlPr eqArr eqArrPr bar barPr pos'''.split())
_OMML_REQUIRED = {'f': ('num', 'den'), 'sSub': ('e', 'sub'),
                  'sSup': ('e', 'sup'), 'sSubSup': ('e', 'sub', 'sup'),
                  'rad': ('e', 'deg'), 'limLow': ('e', 'lim'),
                  'limUpp': ('e', 'lim'), 'bar': ('e',)}


_OMML_CONTENT = set('r f sSub sSup sSubSup rad d limLow limUpp eqArr bar'.split())
_OMML_CHILDREN = {
    'oMath': _OMML_CONTENT,
    'oMathPara': {'oMath', 'oMathParaPr'},
    'r': {'rPr', 't'},
    'eqArr': {'eqArrPr', 'e'},
    'eqArrPr': {'ctrlPr'},
    'bar': {'barPr', 'e'},
    'barPr': {'pos', 'ctrlPr'},
    'f': {'fPr', 'num', 'den'},
    'sSub': {'sSubPr', 'e', 'sub'},
    'sSup': {'sSupPr', 'e', 'sup'},
    'sSubSup': {'sSubSupPr', 'e', 'sub', 'sup'},
    'rad': {'radPr', 'deg', 'e'},
    'd': {'dPr', 'e'},
    'limLow': {'limLowPr', 'e', 'lim'},
    'limUpp': {'limUppPr', 'e', 'lim'},
    **{tag: _OMML_CONTENT | {'argPr'} for tag in ('e', 'num', 'den', 'sub', 'sup', 'deg', 'lim')},
}


def convert_omml(xml: bytes) -> dict[str, Any]:
    """Return unwrapped LaTeX or a review result; never silently flatten XML."""
    try:
        if len(xml) > MAX_FORMULA_BYTES:
            raise ValueError('公式 XML 超出大小限制')
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('公式 XML 不允许 DTD 或实体声明')
        root = ET.fromstring(xml)
        if root.tag not in {f'{{{MATH_NS}}}oMath', f'{{{MATH_NS}}}oMathPara'}:
            raise ValueError('预期 OMML oMath 或 oMathPara 根节点')
        reasons: list[str] = []
        def audit(node: ET.Element, in_properties: bool = False) -> None:
            tag = node.tag.split('}')[-1]
            ns = node.tag[1:].split('}')[0] if node.tag.startswith('{') else ''
            if ns == WORD_NS and (in_properties or tag == 'rPr'):
                return  # font/language/format metadata, never formula content
            if ns != MATH_NS:
                reasons.append(f'不支持的公式命名空间或节点: {tag}')
            elif tag not in _OMML_SUPPORTED:
                reasons.append(f'未验证的 OMML 结构: {tag}')
            if tag in _OMML_REQUIRED:
                for required in _OMML_REQUIRED[tag]:
                    if len(node.findall(f'{{{MATH_NS}}}{required}')) != 1:
                        reasons.append(f'OMML {tag} 缺少 {required}')
            val = node.get(f'{{{MATH_NS}}}val')
            if tag == 'type' and val not in (None, 'bar', 'lin'):
                reasons.append(f'未验证的 OMML 分数类型: {val}')
            if tag == 'pos' and val not in (None, 'top', 'bot'):
                reasons.append(f'未知 OMML 横线位置: {val}')
            if tag == 'eqArr' and not node.findall(f'{{{MATH_NS}}}e'):
                reasons.append('OMML eqArr 缺少行')
            if tag == 'r' and any('~' in (t.text or '') for t in node.findall(f'{{{MATH_NS}}}t')):
                if node.find(f'{{{MATH_NS}}}rPr/{{{MATH_NS}}}nor') is not None:
                    reasons.append('普通文字模式下的 ASCII tilde 暂不转换')
            if tag in ('begChr', 'endChr') and val not in (None, '', '(', ')', '[', ']', '|', '.'):
                reasons.append(f'未验证的 OMML 定界符: {val}')
            if tag == 'sty' and val not in (None, 'p', 'b', 'i', 'bi'):
                reasons.append(f'未知 OMML 字体样式: {val}')
            if tag == 'scr' and val not in (None, 'roman', 'script', 'fraktur', 'double-struck', 'sans-serif', 'monospace'):
                reasons.append(f'未知 OMML 字体类别: {val}')
            if tag == 't':
                text = node.text or ''
                if any(c in text for c in '\\{}$%&#_'):
                    reasons.append('公式文字包含需人工确认转义的 LaTeX 保留字符')
                if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
                    reasons.append('公式含字体私有区字符')
            for child in node:
                child_tag = child.tag.split('}')[-1]
                if tag in _OMML_CHILDREN and child_tag not in _OMML_CHILDREN[tag]:
                    reasons.append(f'OMML 子节点位置异常: {tag}/{child_tag}')
                audit(child, in_properties or tag.endswith('Pr'))
        audit(root)
        # Do not expose known lossy candidate output as usable LaTeX.
        if reasons:
            return _result(None, reasons, OMML_ENGINE)
        latex = _render_omml(root)
        return _result(latex, [], OMML_ENGINE)
    except Exception as exc:
        return _result(None, [f'{type(exc).__name__}: {exc}'], OMML_ENGINE)


def _render_omml(source: ET.Element) -> str:
    """Two narrowly scoped output fixes; all other rendering stays in the package.

    The input has already passed the strict structural audit. Placeholder runs
    let us use only the dependency's public API, without process-global patches.
    """
    from omml2latex import convert_omml as dependency_convert
    root = copy.deepcopy(source)
    marker_prefix = 'QACONVERSIONTOKEN'
    source_text = ''.join(root.itertext())
    while marker_prefix in source_text:
        marker_prefix += 'X'
    replacements: dict[str, str] = {}

    def token(latex: str) -> str:
        key = f'{marker_prefix}{len(replacements)}END'
        replacements[key] = latex
        return key

    def argument(node: ET.Element) -> str:
        wrapper = ET.Element(f'{{{MATH_NS}}}oMath')
        wrapper.extend(copy.deepcopy(child) for child in node if not child.tag.endswith('}argPr'))
        latex = _render_omml(wrapper)
        runs = list(node)
        # Parenthesize compound operands, preserving precedence with a slash.
        simple = (len(runs) == 1 and runs[0].tag == f'{{{MATH_NS}}}r'
                  and bool(re.fullmatch(r'[\w.]+', ''.join(runs[0].itertext()), re.UNICODE)))
        return '{' + latex + '}' if simple else r'\left(' + latex + r'\right)'

    def prepare(parent: ET.Element) -> None:
        for index, child in enumerate(list(parent)):
            kind = child.find(f'{{{MATH_NS}}}fPr/{{{MATH_NS}}}type')
            if child.tag == f'{{{MATH_NS}}}f' and kind is not None and kind.get(f'{{{MATH_NS}}}val') == 'lin':
                num = child.find(f'{{{MATH_NS}}}num')
                den = child.find(f'{{{MATH_NS}}}den')
                latex = argument(num) + '/' + argument(den)
                run = ET.Element(f'{{{MATH_NS}}}r')
                ET.SubElement(run, f'{{{MATH_NS}}}t').text = token(latex)
                parent.remove(child)
                parent.insert(index, run)
            elif child.tag == f'{{{MATH_NS}}}t' and child.text and '~' in child.text:
                # Literal math tilde must be visible, not TeX's spacing operator.
                child.text = child.text.replace('~', token(r'\sim '))
            else:
                prepare(child)

    prepare(root)
    latex = dependency_convert(root).strip()
    delimiter = '$$' if root.tag.endswith('oMathPara') else '$'
    if not (latex.startswith(delimiter) and latex.endswith(delimiter)):
        raise ValueError('OMML 组件返回了异常公式边界')
    latex = latex[len(delimiter):-len(delimiter)].strip()
    for key, value in replacements.items():
        if key not in latex:
            raise ValueError('OMML 适配占位内容未被完整保留')
        latex = latex.replace(key, value)
    return latex


class _StrictMTEFParser(FontMetadataMixin, mtef.MTEFParser):
    """Record unsupported data which the upstream parser intentionally skips."""
    def __init__(self, body: bytes):
        super().__init__(body)
        self.warnings: list[str] = []

    def read_char(self, options):
        return self.bind_font(mtef_extensions.read_char(self, options), options)

    def read_pile(self, options):
        node = super().read_pile(options)
        node.source_options = options
        return node

    def read_matrix(self):
        # The dependency parses cells but discards alignment/partition metadata.
        # Retain those existing bytes so that the narrow adapter can fail closed.
        start = self.r.i
        node = super().read_matrix()
        node.source_alignment = tuple(self.r.d[start:start + 3])
        partition_bytes = ((node.rows + 1) * 2 + 7) // 8 + ((node.cols + 1) * 2 + 7) // 8
        node.source_partitions = self.r.d[start + 5:start + 5 + partition_bytes]
        return node

    def read_tmpl(self):
        start = self.r.i
        node = super().read_tmpl()
        option_offset = 3 if self.r.d[start + 1] & 0x80 else 2
        node.source_options = self.r.d[start + option_offset]
        return node

    def _note_future(self, t: int, blob: bytes) -> None:
        # Future records may contain semantics not represented by known nodes.
        # Do not prefer a stored TeX string which may be stale relative to edits.
        if not mtef_extensions.is_tex_input_metadata(t, blob):
            self.warnings.append(f'未验证的 MTEF 扩展记录: {t}')
        super()._note_future(t, blob)


def _is_condition_stack(node: Any) -> bool:
    """Only a two-row one-column bar/blank layout, not arbitrary matrices."""
    if mtef_extensions.is_legacy_condition_matrix(node):
        return True
    if isinstance(node, mtef.Mtx):
        if (node.rows, node.cols, len(node.cells)) != (2, 1, 2):
            return False
        if getattr(node, 'source_alignment', None) != (1, 1, 1):
            return False
        if getattr(node, 'source_partitions', None) != b'\0\0':
            return False
        rows = node.cells
    elif isinstance(node, mtef.Pile):
        if (node.halign, node.valign, len(node.lines)) != (2, 1, 2):
            return False
        rows = node.lines
    else:
        return False
    return (all(isinstance(row, mtef.Line) for row in rows)
            and len(rows[0].items) == 1 and not rows[1].items
            and isinstance(rows[0].items[0], mtef.Tmpl)
            and rows[0].items[0].selector in (12, 13))


class _RenderedMTEF(mtef.Text):
    """Use the dependency's existing Text dispatch for an adapted subtree."""
    def __init__(self, rendered: str):
        super().__init__('')
        self.rendered = rendered

    def latex(self) -> str:
        return self.rendered


def _arrow_issue(node: Any) -> str | None:
    """MTEF5 tmARROW: top LINE, bottom LINE, expanding arrow CHAR.

    Support single left/right arrows only. Flags and the explicit glyph must
    agree; an empty present label is valid, absent labels must be null/empty.
    """
    var = node.variation
    direction = var & 0x30
    if var & ~0x3c or direction not in (0x10, 0x20):
        return 'MTEF 箭头类型或方向尚未支持'
    if getattr(node, 'source_options', None) != 0 or len(node.slots) != 3:
        return 'MTEF 箭头选项或槽位结构异常'
    upper, lower, glyph = node.slots
    for flag, label in ((4, upper), (8, lower)):
        if not isinstance(label, mtef.Line):
            return 'MTEF 箭头条件不是 LINE 槽位'
        if (not var & flag and (label.items or not label.null)) or (var & flag and label.null):
            return 'MTEF 箭头条件标志与内容不一致'
    expected = 0x2190 if direction == 0x10 else 0x2192
    if (not isinstance(glyph, mtef.Text) or glyph.mtcode != expected
            or glyph.typeface != 22 or glyph.embell):
        return 'MTEF 箭头字符与方向标志不一致'
    return None


def _condition_latex(node: Any) -> str:
    """Keep Chinese condition text in text mode; preserve nested math nodes."""
    def transform(item):
        item = copy.copy(item)
        for attr in ('items', 'slots', 'lines', 'cells'):
            if hasattr(item, attr):
                setattr(item, attr, [transform(child) for child in getattr(item, attr)])
        if (isinstance(item, mtef.Text) and not isinstance(item, _RenderedMTEF)
                and not item.embell and re.fullmatch(r'[\u3400-\u9fff\uf900-\ufaff℃]+', item.s)):
            return _RenderedMTEF(r'\text{' + item.s + '}')
        return item
    result = mtef.to_latex(transform(node)).strip()
    return _merge_text_runs(result)


def _merge_text_runs(result: str) -> str:
    # Adjacent text characters form one word, without altering math boundaries.
    while True:
        merged = re.sub(r'\\text\{([^{}]*)\}\\text\{([^{}]*)\}',
                        lambda match: r'\text{' + match[1] + match[2] + '}', result)
        if merged == result:
            return result
        result = merged


def _adapt_mtef_tree(node: Any) -> Any:
    # Called only after the entire source tree passed validation.
    condition_stack = _is_condition_stack(node)
    custom = plain_custom_font_latex(node)
    if custom is not None:
        return _RenderedMTEF(custom + (' ' if re.search(r'\\[A-Za-z]+$', custom) else ''))
    if isinstance(node, mtef.Text) and not isinstance(node, _RenderedMTEF):
        # These are explicit Unicode characters, not font-position guesses.
        # Emit CJK as text and named mathematical symbols as portable LaTeX.
        scalar = {0x2103: r'{}^{\circ}\mathrm{C}', 0x25A1: r'\square{}'}.get(node.mtcode)
        if re.fullmatch(r'[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff]+', node.s):
            scalar = r'\text{' + node.s + '}'
        if scalar is not None:
            for embell in node.embell:
                pre, post = mtef.EMBELL_LATEX[embell]
                scalar = pre + scalar + post
            return _RenderedMTEF(scalar)
    for attr in ('items', 'slots', 'lines', 'cells'):
        if hasattr(node, attr):
            setattr(node, attr, [_adapt_mtef_tree(child) for child in getattr(node, attr)])
    if isinstance(node, mtef.Tmpl) and node.selector == 14:
        upper, lower = map(_condition_latex, node.slots[:2])
        command = r'\xleftarrow' if node.variation & 0x10 else r'\xrightarrow'
        return _RenderedMTEF(command + ('[' + lower + ']' if lower else '') + '{' + upper + '}')
    if isinstance(node, mtef.Tmpl) and node.selector in (12, 13):
        body = mtef.to_latex(node.slots[0])
        command = r'\underline' if node.selector == 12 else r'\overline'
        for _ in range(2 if node.variation == 1 else 1):
            body = command + '{' + body + '}'
        return _RenderedMTEF(body)
    if condition_stack:
        rows = node.cells if isinstance(node, mtef.Mtx) else node.lines
        alignment = 'l' if isinstance(node, mtef.Mtx) else 'c'
        # Keep the explicit empty second row. Do not synthesize = or arrows.
        body = mtef.to_latex(rows[0])
        return _RenderedMTEF(r'\begin{array}{' + alignment + '}' + body + r' \\ {}\end{array}')
    extra = mtef_extensions.render_node(node)
    if extra is not None:
        return _RenderedMTEF(extra)
    return node


def _audit_mtef(node: Any, reasons: list[str]) -> None:
    if mtef_extensions.audit_node(node, reasons):
        pass
    elif isinstance(node, mtef.Text):
        if node.mtcode is None or node.mtcode == 0:
            reasons.append('MTEF 字符缺少 Unicode MTCode，不能猜测字体字符')
        elif 0xE000 <= node.mtcode <= 0xF8FF:
            reasons.append(f'MTEF 字体私有区字符: U+{node.mtcode:04X}')
        if (node.typeface is not None and (node.typeface < 0 or node.typeface in (7, 9, 10))
                and not is_plain_unicode_custom_font(node)):
            reasons.append('MTEF 自定义字体字符需核对')
        for embell in node.embell:
            if embell not in mtef.EMBELL_LATEX:
                reasons.append(f'未支持的 MTEF 装饰: {embell}')
    elif isinstance(node, mtef.Tmpl):
        # Limited, sample-tested subset. Bars, arrows, big operators and vector
        # layouts in upstream use simplifications/heuristics: review explicitly.
        supported = {(1, 3): 3, (10, 0): 2, (11, 0): 2,
                     (27, 0): 2, (28, 0): 2, (29, 0): 2,
                     (12, 0): 1, (12, 1): 1, (13, 0): 1, (13, 1): 1}
        expected = supported.get((node.selector, node.variation))
        if node.selector == 14:
            error = _arrow_issue(node)
            if error:
                reasons.append(error)
        elif expected is None:
            reasons.append(f'未验证的 MTEF 模板: selector={node.selector}, variation={node.variation}')
        elif len(node.slots) != expected:
            reasons.append(f'MTEF 模板槽位数异常: {node.selector}')
        if node.selector in (12, 13) and getattr(node, 'source_options', None) != 0:
            reasons.append('MTEF 横线模板选项未验证')
    elif isinstance(node, (mtef.Pile, mtef.Mtx)):
        if not _is_condition_stack(node):
            reasons.append('MTEF 多行/矩阵布局需人工核对')
    elif not isinstance(node, mtef.Line):
        reasons.append(f'未支持的 MTEF 节点: {type(node).__name__}')
    for attr in ('items', 'slots', 'lines', 'cells'):
        for child in getattr(node, attr, []):
            _audit_mtef(child, reasons)


def convert_mathtype(ole: bytes) -> dict[str, Any]:
    """Read OLE using olefile, reuse a pinned MTEF module, fail closed on gaps."""
    try:
        if len(ole) > MAX_FORMULA_BYTES:
            raise ValueError('公式 OLE 超出大小限制')
        import olefile
        with olefile.OleFileIO(io.BytesIO(ole)) as container:
            raw = container.openstream('Equation Native').read()
        if len(raw) < 35 or int.from_bytes(raw[:2], 'little') != 28:
            raise ValueError('不支持或损坏的 Equation Native 头')
        if raw[28] != 5:
            raise ValueError(f'未支持的 MTEF 版本: {raw[28]}')
        end = raw.find(b'\0', 33)
        if end < 0 or end + 2 > len(raw):
            raise ValueError('MTEF 版本头不完整')
        parser = _StrictMTEFParser(raw[end + 2:])
        tree = parser.parse()
        reasons = parser.warnings
        if tree is None:
            raise ValueError('MTEF 中未找到公式结构')
        _audit_mtef(tree, reasons)
        # The upstream parse() returns after the first LINE. Only the terminal
        # END is expected here; anything else must not be ignored.
        tail = parser.r.d[parser.r.i:]
        if tail != b'\0':
            reasons.append('MTEF 首个公式后存在未解析数据')
        if reasons:
            return _result(None, reasons, MTEF_ENGINE)
        latex = _merge_text_runs(mtef.to_latex(_adapt_mtef_tree(tree)).strip())
        unsupported = ('\\Alpha', '\\Beta', '\\Epsilon', '\\Zeta', '\\Eta', '\\Iota',
                       '\\Kappa', '\\Mu', '\\Nu', '\\Omicron', '\\Rho', '\\Tau',
                       '\\Chi', '\\sigmaf')
        if any(token + ' ' in latex for token in unsupported):
            return _result(None, ['MTEF 组件生成了未支持的希腊字母命令'], MTEF_ENGINE)
        return _result(latex, [], MTEF_ENGINE)
    except Exception as exc:
        return _result(None, [f'{type(exc).__name__}: {exc}'], MTEF_ENGINE)
