"""Conservative export heuristic, not certification of the customer's renderer."""
import re

VERSION = 'mixed-formula/1'
GREEK = r'\\(?:alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|rho|sigma|tau|phi|omega|Delta|Omega)'
ATOM = rf'(?:[A-Za-z0-9]+|{GREEK})'


def decide(latex: str) -> dict:
    def result(mode, reason):
        return {'version': VERSION, 'mode': mode, 'reason': reason,
                'customer_compatibility': 'unverified'}
    if re.search(r'</?[A-Za-z][^>]*>|\$|[\x00-\x08\x0b\x0c\x0e-\x1f]', latex):
        return result('review', 'formula_contains_markup_or_delimiter')
    if re.search(r'(?<![A-Za-z0-9}])\^|\^(?:$|[^A-Za-z0-9{])', latex):
        return result('image', 'unverified_superscript_syntax')
    if latex.count('_') > 1:
        return result('image', 'multiple_subscripts_emphasis_risk')
    if '_' in latex and '^' in latex:
        return result('image', 'combined_subscript_superscript')
    # Braces around atoms do not change their meaning. Never edit exported TeX.
    normalized = re.sub(r'\{(' + ATOM + r')\}', r'\1', latex)
    if re.fullmatch(ATOM + r'_(?:' + ATOM + r')', normalized):
        return result('text', 'single_atomic_subscript_pending_field_context')
    if '_' in latex:
        return result('image', 'subscript_in_compound_expression')
    # One atomic fraction is allowed. Nested/compound arguments do not match.
    residual = re.sub(r'\\frac\{(' + ATOM + r')\}\{(' + ATOM + r')\}', 'F', latex)
    residual = re.sub(r'\\sqrt\{(' + ATOM + r')\}', 'S', residual)
    residual = re.sub(GREEK, 'G', residual)
    residual = re.sub(r'\{([A-Za-z0-9]+)\}', r'\1', residual)
    if re.fullmatch(r'[A-Za-z0-9\s.+\-*/=(),^]+', residual):
        return result('text', 'simple_expression_or_atomic_fraction')
    return result('image', 'structure_outside_conservative_text_subset')


def apply_field_context(ids: list[str], formulas: dict, policies: dict) -> None:
    scripted = [fid for fid in ids if fid in formulas and '_' in (formulas[fid].get('latex') or '')]
    if len(scripted) >= 2:
        for fid in scripted:
            if policies[fid]['mode'] == 'text':
                policies[fid] = {**policies[fid], 'mode': 'image',
                                 'reason': 'multiple_subscript_formulas_in_same_field'}
