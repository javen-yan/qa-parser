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
    (out / 'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['filename']}\n" for r in rows), encoding='utf-8')
    manifest = {'schema_version': 1, 'tag': tag, 'commit': commit, 'prerelease': prerelease, 'artifacts': rows}
    (out / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    notes = f'''# 题库转换 {tag}

版本：{version}；源码：{commit}；类型：{'预发布验证候选' if prerelease else '正式发布'}；云端轻量版。

| 平台/架构 | 文件 | 字节数 | SHA256 |
|---|---|---:|---|
'''
    notes += ''.join(f"| {r['target']} | {r['filename']} | {r['bytes']} | `{r['sha256']}` |\n" for r in rows)
    notes += '''
## 使用方式

完整解压对应平台ZIP。Windows进入“题库转换”文件夹双击EXE，保留_internal；Mac打开.app。无需安装Python、Office、MathType、Node或字体。选择题型→添加Word→转换→预览→打开结果包。不要单独复制EXE或HTML。

## 验证范围与限制

每个平台分别原生构建，并对实际解压后的同一ZIP执行合成题目、图片/公式、WMF和原生窗口检查；开发核心与冻结程序结果比较通过。详细平台证据见release-manifest.json。

真实9份题库未上传CI；不声称跨平台900/900。此云端轻量包没有可选LibreOffice原图引擎，复杂原图可能被明确隔离。Mac本地已验收原图完整版另行保留。Windows x86尚未支持，Windows ARM不在本次范围。

客户Windows11断网/非管理员/安全策略、Mac签名公证及目标APP导入渲染仍需验收。程序仅从本机读取题目，运行时不下载依赖。请同时保留包内licenses和使用说明；发布状态不代表客户环境已经验收。
'''
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
