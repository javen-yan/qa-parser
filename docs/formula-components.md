# 公式组件选型与使用边界

本阶段复用的是小型依赖包及一个独立源码模块，不引入完整题库项目。转换核心在 macOS Python 3.13 本地验证；客户分发方式尚未实现，不能将开发环境要求转嫁给最终客户。

| 能力 / 候选 | 选择与样本结论 | 维护、许可、运行依赖 |
|---|---|---|
| OMML → LaTeX：`omml2latex==0.1.1` | 采用公开 `convert_omml` API，加严格输入检查。上下标、分数及反应条件可转换；复杂 eqArr/bar、非默认分式、需转义文字等进入复核。 | Apache-2.0，2026-04-09 发布，PyPI 标为 Alpha；纯 Python、无运行依赖，离线可用。未承诺长期维护。 |
| MathType：`formula-flow/scripts/mtef.py` 单文件 | 仅复用其中的 MTEF 对象树解析与 LaTeX 转换，不引入整个项目、CLI 或 Microsoft XSL。严格适配层拒绝未知模板、布局、私有码和未解析尾部。 | MIT，固定提交与哈希见下；纯 Python。不是通用完整 MTEF 支持，仅 v5 且已验证子集。 |
| OLE 容器：`olefile==0.47` | 采用包读取 Equation Native；不采用候选脚本内自研 CFBF 回退。 | BSD-2-Clause，纯 Python，跨平台库；本次仅 Mac 实测。 |
| OMML → MathML → LaTeX 两段转换 | 不采用：已有直接转换的小包能覆盖主要样本，引入两段转换、额外运行时或来源不清的 Office XSL 会增加维护负担。 | `scienceai/omml2mathml` Apache-2.0 Node 实现；`mathml2latex` MIT Python。未作为运行依赖安装或宣称通过。 |
| Rust `hiro93/mtef-rs` | 不采用：无需为 Rust 坚持引入 GPL-3.0 组件或移植完整实现。 | 调研仓库为 GPL-3.0；未集成。 |
| `unfair-codec-mtef` | 不采用：公开说明主要为 MTEF 3.x/StarMath 输出，与样本 v5/LaTeX 链路不直接匹配。 | 未集成，未验证样本，不能算作已通过候选。 |

## 单文件复用来源

- 仓库：https://github.com/goldfish-x/formula-flow
- 固定提交：`9d5737aaa4514f2d12b5206a7357382fdfada2fb`
- 原文件：`scripts/mtef.py`
- 下载原文件 SHA-256：`a22d84d4f5064bf2b186992c0bda946012365ad10e3e481a70422da4f1d1f100`
- 本地：`src/qa_parser/vendor/mtef.py`，MIT 文本保留于相邻 `mtef.LICENSE`。
- 本地改动：只保留常量、数据读取、对象树、解析器及 LaTeX 转换部分；删除 CLI、自检、文档提取、CFBF 回退和 stdout 编码修改。未打包上游 Microsoft `MML2OMML.XSL`。
- 严格检查写在 `formulas.py`，避免将上游“退化为拼接”“忽略私有字形”等行为误标成功。上游更新时必须重新跑样本及失败案例，而非直接覆盖。

## API 与状态

`convert_omml(xml: bytes)` / `convert_mathtype(ole: bytes)` 返回同一结构：

```json
{"latex":"R_0","status":"converted","reason":"","engine":"component/version+adapter/version"}
```

`converted` 仅表示组件和严格适配层接受该结构，不代表人工逐公式确认，更不代表目标 APP 渲染通过。`review` 表示需人工处理，`latex` 为 null，原因明确保留；源 XML/OLE 和位置由调用方的资源/中间结构保存。适配异常转为 review，不中断整份文档。

不为了提高通过率猜测化学箭头、使用未知私有码映射，或优先信任可能过时的嵌入 TeX 源文。当前保守范围可在新增固定测试并核验语义后逐步扩展。

## 来源

- https://pypi.org/project/omml2latex/0.1.1/
- https://github.com/goldfish-x/formula-flow/blob/9d5737aaa4514f2d12b5206a7357382fdfada2fb/scripts/mtef.py
- https://pypi.org/project/olefile/0.47/
- https://github.com/scienceai/omml2mathml
- https://pypi.org/project/mathml2latex/
- https://github.com/hiro93/mtef-rs
- https://docs.rs/unfair-codec-mtef/latest/unfair_codec_mtef/
