#!/usr/bin/env python3
"""Run audited synthetic tests without reading private Reference data.

Private cases are explicitly deselected and listed in the report, never counted
as passes. Unexpected skips fail this check so missing data cannot masquerade as
acceptance. This runner is independent of the nine-document regression suite.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MODULES = {'test_libreoffice_preview.py': 'private original-WMF fixtures; optional renderer not Windows-certified'}
PRIVATE_TESTS = {
    'test_classification.py': {'test_nine_reference_documents_body_classification'},
    'test_document.py': {'test_reference_question_boundaries_and_resource_accounting'},
    'test_export_preview.py': {'test_reference_choice_reads_real_export_and_all_hundred_rows'},
    'test_formula_images.py': {'test_real_original_mathtype_preview'},
    'test_formulas.py': {
        'test_real_physics_pair_preserves_first_subscript_and_fraction',
        'test_real_chemical_arrow_is_not_silently_reduced_to_condition_text',
        'test_real_newly_supported_omml_cases_preserve_content',
        'test_real_mathtype_double_underbar_keeps_count_direction_and_empty_row'},
    'test_mtef_arrows.py': {'test_real_question_one_raw_ole_conditions_match_source',
        'test_real_conditions_preserve_complete_text_and_nested_scripts', 'test_all_thirty_original_arrow_objects'},
    'test_mtef_extensions.py': {'test_real_color_before_prime_and_cached_source_convert_from_structure',
        'test_real_condition_vector_and_nonempty_lower_row_remain_in_place', 'test_real_limit_oxidation_condition_and_pre_script'},
    'test_mtef_fonts.py': {'test_seven_original_objects_resolve_their_actual_font_definitions'},
    'test_script_context.py': {'test_actual_ten_source_groups_are_preserved'},
}
PLATFORM_TESTS = {'test_desktop_window.py': {'test_stop_service_reaps_owned_renderer_even_after_leader_exits'}}


def exclusion_reason(filename, function):
    if filename == 'test_macos_update.py' and sys.platform == 'win32':
        return 'macOS bundle/symlink and manager handoff contracts; native macOS CI covers them'
    if filename == 'test_windows_update.py':
        import importlib.util
        if importlib.util.find_spec('cryptography') is None:
            return 'Windows updater crypto tests run in Windows native CI; not a macOS runtime dependency'
    if filename in PRIVATE_MODULES:
        return PRIVATE_MODULES[filename]
    if function in PRIVATE_TESTS.get(filename, set()):
        return 'private Reference fixture; not part of public synthetic regression'
    if function in PLATFORM_TESTS.get(filename, set()):
        return 'POSIX-specific process-group contract; Windows lifecycle checked separately'
    return None


class Audit:
    def __init__(self):
        self.report = {'schema_version': 1, 'scope': 'public synthetic regression only',
            'private_reference_acceptance': 'not performed', 'collected': 0, 'selected': 0,
            'deselected': [], 'unexpected_skips': [], 'passed': 0, 'failures': []}

    def pytest_collection_modifyitems(self, config, items):
        self.report['collected'] = len(items)
        selected, excluded = [], []
        for item in items:
            reason = exclusion_reason(Path(str(item.path)).name, getattr(item, 'originalname', None) or item.name.split('[')[0])
            if reason:
                excluded.append(item)
                self.report['deselected'].append({'nodeid': item.nodeid, 'reason': reason})
            else:
                selected.append(item)
        items[:] = selected
        self.report['selected'] = len(selected)
        config.hook.pytest_deselected(items=excluded)

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.report['unexpected_skips'].append({'nodeid': report.nodeid, 'reason': str(report.longrepr)})
        elif report.failed:
            self.report['failures'].append({'nodeid': report.nodeid, 'phase': report.when})
        elif report.when == 'call':
            self.report['passed'] += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--junitxml', type=Path)
    args = parser.parse_args(argv)
    import pytest
    audit = Audit()
    command = [str(ROOT / 'tests'), '-q', '-ra']
    if args.junitxml:
        command += ['--junitxml', str(args.junitxml.resolve())]
    code = int(pytest.main(command, plugins=[audit]))
    if not audit.report['selected'] or audit.report['unexpected_skips']:
        code = 1
    audit.report['status'] = 'passed' if code == 0 else 'failed'
    audit.report['exit_code'] = code
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(audit.report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Public tests: {audit.report['passed']} passed; {len(audit.report['deselected'])} explicitly excluded; {len(audit.report['unexpected_skips'])} unexpected skips.")
    return code


if __name__ == '__main__':
    raise SystemExit(main())
