#!/usr/bin/env python3
"""Assemble only a complete set of successful native candidates for a tag.

No publication/network actions. The release job separately uploads a draft and
publishes only after all assets are present. Stable publication requires explicit approval in the version-controlled policy;
customer acceptance is tracked separately and disclosed in release notes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


WINDOWS_EXTRAS = ('qa-parser-windows-x64.qaupdate', 'qa-parser-windows-x64-runtime.zip', 'windows-distribution-size.json')


def extra_artifacts(folder, data, verify=True):
    extras = data.get('windows_distribution')
    if extras is None:
        return []  # Historical candidate metadata remains readable.
    if data.get('target') != 'windows-x64' or extras.get('schema_version') != 1:
        raise ValueError('Unexpected split distribution metadata')
    checks = extras.get('validation', {})
    if not all(checks.get(k) is True for k in ('native_windows', 'signed_upgrade', 'startup_rollback', 'runtime_unchanged')) or checks.get('status') != 'passed':
        raise ValueError('Windows update native validation incomplete')
    rows = extras.get('artifacts', [])
    if len(rows) != len(WINDOWS_EXTRAS) or {r.get('filename') for r in rows} != set(WINDOWS_EXTRAS):
        raise ValueError('Windows supplemental artifact set incomplete')
    paths = []
    for row in rows:
        if not re.fullmatch('[0-9a-f]{64}', str(row.get('sha256', ''))) or type(row.get('bytes')) is not int or row['bytes'] <= 0:
            raise ValueError('Invalid supplemental artifact metadata')
        path = folder / row['filename']
        if verify and (not path.is_file() or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']):
            raise ValueError('Windows supplemental artifact hash/size mismatch')
        paths.append(path)
    return paths


def validate_candidates(artifacts, commit):
    policy = json.loads((ROOT / 'packaging/release-policy.json').read_text())
    checked = []
    for target in policy['required_targets']:
        name = f'qa-parser-{target}-candidate.zip'
        candidates = list(artifacts.rglob(name))
        if len(candidates) != 1:
            raise ValueError(f'Expected exactly one verified archive for {target}, found {len(candidates)}')
        archive = candidates[0]
        sidecar = archive.parent / 'release-metadata.json'
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        if not (data['status'] == 'passed' and data['target'] == target and data['commit'] == commit
                and data['zip_sha256'] == digest(archive) and data['variant'] == 'light'):
            raise ValueError(f'Candidate metadata/hash/commit mismatch: {target}')
        if not data.get('native_smoke_passed') or not data.get('source_closure_complete'):
            raise ValueError(f'Native smoke/source distribution closure incomplete: {target}')
        with ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError(f'ZIP integrity failed: {target}')
        extra_artifacts(archive.parent, data)
        checked.append((target, archive, data))
    return checked


def assemble(artifacts, out, tag, commit):
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:rc|beta|alpha)\.\d+)?', tag):
        raise ValueError('Expected a user-created version tag vX.Y.Z or vX.Y.Z-rc.N/beta.N/alpha.N')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    if tag[1:].split('-')[0] != version:
        raise ValueError('Tag base version must match pyproject.toml; do not invent a release version')
    policy = json.loads((ROOT / 'packaging/release-policy.json').read_text())
    prerelease = '-' in tag
    if not prerelease and not policy['stable_release_approved']:
        raise ValueError('Stable publication blocked: ' + '; '.join(policy['stable_release_blockers']))
    checked = validate_candidates(artifacts, commit)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for target, archive, data in checked:
        filename = f'qa-parser-{tag}-{target}-light.zip'
        shutil.copy2(archive, out / filename)
        rows.append({'target': target, 'filename': filename, 'sha256': data['zip_sha256'],
                     'bytes': archive.stat().st_size, 'evidence': data})
    extras = []
    for target, archive, data in checked:
        for path in extra_artifacts(archive.parent, data):
            shutil.copy2(path, out / path.name)
            extras.append({'filename': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size})
    (out / 'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['filename']}\n" for r in rows + extras), encoding='utf-8')
    manifest = {'schema_version': 1, 'tag': tag, 'commit': commit, 'prerelease': prerelease, 'artifacts': rows, 'supplemental_artifacts': extras}
    (out / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    notes = f'''# 题库转换 {tag}

版本：{version}；源码：{commit}；类型：{'预发布验证候选' if prerelease else '正式发布'}；云端轻量版。

| 平台/架构 | 文件 | 字节数 | SHA256 |
|---|---|---:|---|
'''
    notes += ''.join(f"| {r['target']} | {r['filename']} | {r['bytes']} | `{r['sha256']}` |\n" for r in rows)
    notes += """
## Windows v1 双版本分发

首次完整解压 ZIP，双击根目录题库转换.exe。管理更新.cmd 提供离线更新与恢复上一版本。
.qaupdate 包仅更新业务程序；固定 WebView2、WMF 及对应源码归档由独立运行时版本管理。
业务包包含 Python、界面和字体，不需要安装开发环境。运行时不兼容时使用新完整包。
Mac ARM64/Intel 继续完整 .app 分发，资源布局与签名流程不变。

## 导出约定

显示引用 `参考aaa001.png)` 对应实际 `001.png`，难度保持易／中／难。
多工作表导入尚未验证；本次分发改造不改变既有解析及导出规则。

## 验证范围

发布必须通过三平台原生构建、窗口/转换检查与 Windows 签名更新、启动失败回退验证。
私有题库不上传 CI，客户 Windows 11 实机、Mac 签名公证及目标系统导入仍需验收。
以 release-manifest.json 中原生证据为准；不宣称任意文档 900/900 全覆盖。
不要单独复制 EXE；请保留全部许可证及源码归档。程序离线运行，不自动下载依赖。
"""
    (out / 'RELEASE_NOTES.md').write_text(notes, encoding='utf-8')
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--artifacts', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--commit', required=True)
    a = p.parse_args()
    assemble(a.artifacts, a.out, a.tag, a.commit)
