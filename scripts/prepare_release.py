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
## 本版更新

- 修复共享材料题组解析中的编号说明（例如题号使用“116．”，说明使用“1、2、3、”）被误拆为新题的问题，避免误报重复题号并隔离前面的正常题目。说明段落保留在所属题组解析中。
- 编号不同但拥有独立选项和答案的真实题目仍单独识别；重复题号、答案归属不明等问题继续明确报错并排除导出。
- 本地问题样本回归：182 道实际题目全部导出，原先通过的 179 题题干、选项和答案保持不变；公开回归 420 项通过。客户环境与目标系统导入仍未验收。

## 延续功能

- 支持共享材料复合题：每个可独立作答子题单独导出，继承材料、原图及来源位置，集中答案和解析按明确题号关联。组织结构与作答方式分别识别，未知题逐题报告，不因一题失败拒绝整批。
- 填空题保留题干空位；按已验证的答案对应关系回填完整考点。多空答案按顺序用 ASCII 逗号连接，单空内部中文逗号和斜线同义答案不拆分，连续编号空不误拆成多道顶层题。
- 区分排版表、静态材料表和作答交互表。仅表格内交互填空按策略跳过并保留具体证据，静态材料继续保留。报告分别列原题/题组、展开子题、输出单元、导出、策略跳过及失败/复核数量。
- 纯选择和纯填空保持原模板；混合文档使用同名 Excel 内的“选择”“考点”两个工作表。客户系统是否支持多工作表导入尚未验证。
- 按原 Word 文件名输出同名 Excel 和图片目录，保留前导零、中文及空格；同名目录只放图片，说明和预览单独存放。
- 可靠公式优先保留 `$$...$$` LaTeX 文本，不再因多个下标或同一字段多个公式强制转图；原始示意图不改内容，不可靠公式仅使用可信原始预览兜底，否则进入复核。
- 显示引用与图片文件显式分离：Excel 中 `参考aaa001.png)` 对应实际 `001.png`；至少三位序号，超过 999 不截断。题干、选项、答案、解析、预览和缺图校验使用一致映射。
- 难度输出为易／中／难，旧低／中／高只做词汇映射，原随机档位与可重现性保持不变。
- Windows 启动自动诊断和受限恢复：仅对本应用固定可信哈希、签名状态通过验证且已复现来源标记阻止加载的组件处理，不提权、不改变安全策略或解锁其它文件。
- Windows 与 Mac 共用分类、导出、资源编号和异常处理；原生 WebView 生成图片，资源随包提供。修复 Windows 系统编码、环境变量大小写，以及 Mac 退出清理和 Retina 快照比例问题。

## 使用方式

完整解压对应平台ZIP。Windows进入“题库转换”文件夹双击EXE，保留_internal；Mac打开.app。无需安装Python、Office、MathType、Node或字体。选择题型→添加Word→转换→预览→打开结果包。不要单独复制EXE或HTML。

## 验证范围与限制

每个平台分别原生构建，并对实际解压后的同一ZIP执行合成题目、图片/公式、WMF和原生窗口检查；开发核心与冻结程序结果比较通过。输出门槛覆盖同一字段多下标公式保持文本、显示引用与物理文件映射、易中难档位、同名包布局、重复名拒绝和未知结构复核；开发态与冻结态结果一致。详细平台证据见release-manifest.json及对应源码构建的CI日志。

v0.3.3 历史 Mac 本地真实样本回归（以下历史结果非本版重新验收）：原九份900题导出896题，2题按表格交互策略跳过、2题复核。相比v0.3.2原899题导出，新增隔离为化学填空第19/49题表格交互和化学填空2第30题无空位的开放计算子问；第62题原公式问题继续复核，不是900/900全覆盖。原900题的题干、选项、解析、原始分段和难度保持不变。

新八份样本共722个原题/题组、230个展开子题、863个输出单元，导出748题，96题策略跳过、19题复核。两份填空2正文XML重复，按正文去重后七份为622个原题/题组、230个展开子题、763个输出单元，664题导出、85题策略跳过、14题复核。展开子题是输出单元的子集，不能与原题/题组数直接相加。含重复样本的19个非策略问题为9题未支持作答方式、8题空位/答案对应不足、2题公式上下标问题；不能猜配图片内空位或开放问答。

本地17份重复处理语义一致，原公式源字节、LaTeX和原图哈希保持不变。旧v1任务按原配置读取，不被新策略改写。源码及打包程序在三平台运行纯合成复合题、填空回填、表格策略及混合工作表检查；真实题库未上传CI，不声称真实题库已在Windows完成验收。此云端轻量包没有可选LibreOffice原图引擎，复杂原图可能被明确隔离。Windows x86尚未支持，Windows ARM不在本次范围。

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
