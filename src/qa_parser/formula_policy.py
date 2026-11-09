"""Prefer unmodified LaTeX accepted by the source structural converter.

This is a corruption guard, not a complete TeX parser or customer renderer
certification. Source diagrams stay images; uncertain objects retain evidence.
"""
import re

VERSION = 'formula-text-first/2'


def decide(latex: str) -> dict:
    def result(mode, reason):
        return {'version': VERSION, 'mode': mode, 'reason': reason,
                'customer_compatibility': 'unverified'}
    if not latex.strip() or re.search(r'</?[A-Za-z][^>]*>|\$|[\x00-\x08\x0b\x0c\x0e-\x1f]', latex):
        return result('review', 'formula_contains_markup_or_delimiter')
    if re.search(r'[_^]\s*(?:[_^}]|$)', latex):
        return result('review', 'invalid_script_argument')
    depth = 0
    for token in re.findall(r'\\.|[{}]', latex):
        if token == '{':
            depth += 1
        elif token == '}':
            depth -= 1
            if depth < 0:
                return result('review', 'unbalanced_formula_braces')
    if depth:
        return result('review', 'unbalanced_formula_braces')
    if re.search(r'\\(?:chemfig|includegraphics)\b|\\begin\{(?:tikzpicture|picture)\}', latex):
        return result('review', 'diagram_requires_original_image')
    return result('text', 'source_structural_conversion_text_first')


def apply_field_context(ids: list[str], formulas: dict, policies: dict) -> None:
    # The client must parse math delimiters before Markdown emphasis.
    # Sharing a cell alone must never force correct formulas into images.
    return
