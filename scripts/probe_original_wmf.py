#!/usr/bin/env python3
"""Diagnostic original-WMF replay using an explicitly supplied LibreOffice runtime.

No production fallback is enabled. Fonts and runtime are caller supplied;
never installs fonts, modifies WMF bytes, or interprets MTEF matrix alignment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from xml.sax.saxutils import escape


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--soffice', required=True, type=Path)
    p.add_argument('--libreoffice-fonts', required=True, type=Path)
    p.add_argument('--cjk-font-directory', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('wmf', nargs='+', type=Path)
    a = p.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [v.resolve() for v in a.wmf]
    if len({v.stem for v in inputs}) != len(inputs):
        p.error('Inputs must have unique stems')
    with tempfile.TemporaryDirectory(prefix='qa-wmf-probe-') as tmp:
        tmp = Path(tmp)
        config = tmp / 'fonts.conf'
        config.write_text('''<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'''
            + ''.join(f'<dir>{escape(str(v.resolve()))}</dir>' for v in [a.libreoffice_fonts, a.cjk_font_directory])
            + f'<cachedir>{escape(str(tmp / "cache"))}</cachedir>'
            + ''.join(f'<alias><family>{old}</family><prefer><family>{new}</family></prefer></alias>' for old, new in [('宋体','Noto Sans CJK SC'),('SimSun','Noto Sans CJK SC'),('Times New Roman','Liberation Serif'),('Symbol','OpenSymbol')])
            + '</fontconfig>', encoding='utf-8')
        env = dict(os.environ, FONTCONFIG_FILE=str(config))
        cmd = [str(a.soffice.resolve()), '-env:UserInstallation=' + (tmp / 'profile').as_uri(), '--headless', '--convert-to', 'png', '--outdir', str(out), *map(str, inputs)]
        run = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
        record = {'diagnostic_only': True, 'runtime': str(a.soffice.resolve()), 'fontconfig': config.read_text(), 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr, 'sources': [{'path': str(v), 'sha256': hashlib.sha256(v.read_bytes()).hexdigest(), 'output_exists': (out / (v.stem + '.png')).is_file()} for v in inputs]}
        (out / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        if run.returncode or not all(v['output_exists'] for v in record['sources']):
            raise SystemExit('Rendering failed; see run.json')
        print(out)

if __name__ == '__main__':
    main()
