# 公式解析与渲染组件复核

> 当前显示与可选原图分发结果以 [离线显示验收](display-release.md) 为准；本文保留此前调查与阶段证据。

核查日期：2026-11-07。本记录区分已在本项目运行的组件、只查阅了源码的候选和需要另外实测的完整产品。网上项目的成功率不是本项目的验收证据，公开源码也不自动意味着全部资源具有同一种许可证。

当前问题并不是“MathType 无法解析”。MTEF 有结构化记录，已有独立实现；条件箭头的缺口来自我们选择的轻量模块没有实现相应转换分支。适合继续复用成熟记录解析、字形映射及结构转换，并用原始对象做交叉核对。短期维护可审计的有限适配，长期应比较更完整的 MTEF → MathML 实现，避免逐题修补。固定中间状态见 [877/23 快照](coverage-877-snapshot.md)。

## 能力、成本与证据

| 方案 | 能处理什么 | 依赖、离线与分发 | 许可、维护证据 | 本项目实际验证 |
|---|---|---|---|---|
| 当前 `goldfish-x/formula-flow` 的 `mtef.py` 固定子集 | MTEF v5 → 对象树 → LaTeX；我们在外层补严格校验和有限结构适配 | Python；OLE 使用独立 `olefile`。不需要 Office/MathType，可随现有解释器打包。未引入其完整 Word 转换链 | 固定提交 `9d5737a…`；相邻 MIT 许可已保留。公开仓库页面只有少量提交，不能当作长期成熟 SDK | 已实际运行九份样本；本次查看原始箭头树并补齐支持。存在真实覆盖缺口，不宣称全 MTEF 支持。[源码项目](https://github.com/goldfish-x/formula-flow) |
| Ruby `sbulka/mathtype` + `jure/mathtype_to_mathml` | 原始 MathType 二进制 → MTEF XML → MathML；真实 MTEF 解析器，不是图片识别 | Ruby/JRuby、gems、XML/XSLT；依赖全部本地时可离线。若进入客户包，需要一并携带运行时、字体映射和样式表 | 两个仓库都声明 MIT；上游有测试目录、历史分支及后续集成。网页未提供可靠的最后维护日期，本次不作“仍活跃维护”承诺 | 仅文档/源结构调研，未对九份原文运行。可作为独立结构对照候选。[二进制解析](https://github.com/sbulka/mathtype)、[MathML 转换](https://github.com/jure/mathtype_to_mathml) |
| `transpect/mathtype-extension` | 出版工作流中的 MathType OLE/WMF → MathML；明确支持 fontmaps、空白处理等 | README 给出 JRuby + Ruby gems + XML Calabash + Saxon classpath；XSLT 是 2.0，不能直接把整条链交给 lxml 的 XSLT 1.0 引擎。需要本地 Java 运行时及依赖，包体成本尚未实测 | Ruby 组件 MIT；扩展自身、fontmaps、Java/JRuby、Calabash、Saxon 须逐项保留许可。仓库根 `LICENSE` 未找到，不能把组合包一律标 MIT/GPL。SaxonJ-HE 9.5 起为 MPL-2.0，PE/EE 是商业产品 | 本次查阅 README、转换 XSLT 和依赖声明，未跑九份样本。优先作为开发端交叉验证，是否随客户包再评估。[项目及依赖](https://github.com/transpect/mathtype-extension)、[XSLT 2.0 源码](https://github.com/transpect/mathtype-extension/blob/master/xsl/transform.xsl)、[Saxon 许可](https://www.saxonica.com/html/documentation12/conditions/index.html) |
| `mathtypejx` / `uwjiaoxing/mathtype2omml` | Python MTEF v3/v5 → MathML → OMML；README 涵盖前置上下标、矩阵、字体映射和 WMF 内嵌 MTEF | Python 3.10+、lxml、olefile、python-docx；最终 OMML 步骤需要 Microsoft `MML2OMML.XSL`。可评估仅运行前段 MTEF → MathML，避免引入该 Office 样式表 | 项目代码 MIT，fontmap 另保留 BSD-style notice；仓库内出现 Office XSL 不足以证明我们可再分发。上游声称在私有 344 文档/7,888 公式集与 Ruby 比对，属作者证据，不是我们的结果 | 已本地输入余下 3 个原始 OLE，产生完整内容的 MathML；但其 `records5.py` 把未知对齐字节 129 静默默认成 center，未解释高位，不能直接作为自动兜底。详见 [独立对照记录](mtef-coverage-extension.md)。未完成整库实测。[候选及原始项目链接](https://github.com/uwjiaoxing/mathtype2omml) |
| Rust `hiro93/mtef-rs` | MTEF → LaTeX 的独立实现 | Rust 构建；能否减少整体包体还取决于图像和桌面运行时。目标架构与本样本输出均须实测 | 仓库声明 GPL-3.0；不能仅凭“Rust 单二进制”忽略分发条件，也不应把 GPL 泛化为不可商用 | 未集成、未跑九份样本；不是本次修复依赖。[项目](https://github.com/hiro93/mtef-rs) |
| LibreOffice | 完整文档排版/格式转换，可做辅助页面视觉参考；这不等于提供完整 MTEF v5 → LaTeX API | 原生多平台大型套件，可本地离线运行。携带套件、用户配置隔离、字体及进程管理会增加分发复杂度。本机已有 LibreOfficeDev 运行目录约 414 MB；没有将其加入客户包 | 官方 MPL-2.0 及其他组件许可证。所查 `starmath/source/mathtype.cxx` 在 `Parse` 中拒绝 `nVersion > 3`；不能拿其旧 MathType 导入器证明 v5 条件箭头完整支持 | 本机 LibreOfficeDev 26.8.0.0.alpha0 已试渲染 5 个原始 WMF，均退出 0，但部分中文仍方框；未完成九文档排版实测，不能作自动兜底。详见 [实测记录](mtef-font-bindings.md)。[源码](https://raw.githubusercontent.com/LibreOffice/core/master/starmath/source/mathtype.cxx)、[官方许可](https://www.libreoffice.org/licenses/) |
| Aspose.Words 商业 SDK | 文档读写、OfficeMath/OMML 与图像/HTML 输出；不能假设所有 OLE MathType 都可转结构化 MathML | Python via .NET、Java/.NET 等产品有不同依赖；官方列出 Windows/macOS/Linux 支持及原生前置条件。可评估本地授权文件模式；计量授权和试用限制须分别确认 | 商业授权；试用会有水印/文档规模限制。官方支持人员在 2024-12-05 明确称不能将 OLE 公式转为 MathML；这是一条有日期的能力证据，不保证当前版本未改变 | 未购买、未装、未运行样本；必须用候选版本试算并确认 OLE 能力后再考虑采购，不能用“商业成熟”代替验收。[官方支持答复](https://forum.aspose.com/t/how-to-handle-ole-formula-when-converting-word-to-html/295294)、[系统要求](https://docs.aspose.com/words/python-net/system-requirements/)、[授权/试用](https://docs.aspose.com/words/python-net/licensing/) |
| `omml2latex==0.1.1` | 原生 OMML XML → LaTeX；与 MathType OLE 是不同入口 | Python 3.9+，发布说明称无外部依赖；离线可用，当前已打包 | Apache-2.0；PyPI 2026-04-09 发布、Alpha 分类、一名维护者，不宜描述为长期成熟平台 | 已用于本项目 OMML 样本及回归。不能拿 OMML 成功替代同题 MathType 原始对象的验证。[固定版本](https://pypi.org/project/omml2latex/0.1.1/) |
| `lxml` / `olefile` | XML、XPath、XSLT 1.0 / OLE 复合文件容器读取 | lxml 依赖 libxml2/libxslt 原生库，olefile 是 Python 包；平台轮子/运行库须随目标包固定 | lxml 主体 BSD，底层库有各自许可；olefile BSD-2-Clause。成熟基础设施不提供 MTEF 数学语义转换 | 已在当前内核使用。XSLT 版本限制需与目标样式表匹配。[lxml 许可](https://github.com/lxml/lxml/blob/master/LICENSE.txt)、[lxml FAQ](https://lxml.de/FAQ.html)、[olefile](https://pypi.org/project/olefile/0.47/) |
| MathJax / KaTeX | 将 LaTeX/MathML 等显示为可阅读公式；不是 MTEF 或 Word 解析器 | 可将 JS、字体和扩展放在本地离线使用。不同宏支持需核对；浏览器字体完整性与源公式结构是两个验收层次 | MathJax Apache-2.0；KaTeX MIT。都是独立维护的排版项目，不意味着任意 MathType 对象可读 | 当前 KaTeX 已随包离线使用并做原生窗口预览；MathJax 未在本项目集成。更换渲染器不能自动修复缺失的方向、上下条件或字体字符映射。[MathJax](https://github.com/mathjax/MathJax)、[KaTeX](https://github.com/KaTeX/KaTeX) |

## 当前采用的少量互补组件

生产路径继续采用 **olefile/lxml 容器读取 + 固定 MTEF 解析模块与薄结构适配 + omml2latex + 严格校验 + 保守 libwmf 图片兜底 + 离线 KaTeX**。这些组件职责互补，不在每道题上遍历大量库“撞成功”，也不让客户安装 Office。MTEF/OMML 先解析成有源位置的结构，明确支持的节点生成公式，未知结构留下具体失败原因和原始证据；只有已验证图形/字体编码的原始预览才进入图片路径。

换成 Rust 可以改变运行时和构建方式，**不能自动补上 MTEF 模板、MTCode、字体绑定或矩阵字节的语义**。当前不为了语言统一重写完整格式，也不为了通过率加入大型 Office 运行时。外部候选作为开发验证工具，只有经相同固定样本、未知输入和分发验证后才可能成为内置能力。

### 三种覆盖率必须分开

- **规范能力覆盖**：下面的模板族、变体位、槽位约束及已知限制；不是所有 MTEF/OMML 都已支持。
- **测试覆盖**：真实源对象、合成方向/上下位置组合、畸形结构和未知字段负例；测试通过证明这些输入的行为。
- **样本通过率**：本次后续开发阶段为 **899/900 题**，与固定的 **877/900 快照**属于不同阶段。它不是规范全覆盖，也不是所有公式已逐个人工核验、Windows 通过或真实 APP 已验收；最终发布结果应以对应构建的回归 JSON 为准。

| 能力族 | 当前结构处理 | 已有测试/证据 | 明确限制和未知行为 |
|---|---|---|---|
| MTEF 单线条件箭头 14 | 方向位、上下槽位和尾部 Unicode 箭头互相核对；中文文本、加热 Δ、嵌套下标按来源输出 | `tests/test_mtef_arrows.py`：原始 30 个箭头、Q1 三处、上下条件及方向合成例、畸形负例 | 双线、鱼叉、方向与字符冲突、未知选项等仍明确失败，不只提取条件文字 |
| MTEF 分式/根式/普通上下标/括号 | 复用记录树，检查已支持 selector/variation 与槽位数量 | `tests/test_formulas.py` 原始物理对应例和结构缺项负例 | 未列入支持的变体、缺失必要槽位、未知模板保持 review |
| 条件上下限与前置上下标 | 模板 23 保留主部/下部/上部；前置 script 必须有后续基底上下文 | `tests/test_mtef_extensions.py`：氧化数、光照条件、前置位置及缺基底 | 不把模板名机械替换为 `lim`；隐藏非空槽位或无基底拒绝 |
| 横线、向量与多行/矩阵 | 双/单横线、上/下向量、明确方向；保留空行/空单元格、已知对齐及嵌套 pile | `tests/test_mtef_extensions.py`、`test_formulas.py` | 仍拒绝未经解释的 `(1,129,129)` 对齐字节和不支持分隔线；候选库默认居中不能作为规范证据 |
| MTCode 空白与字体绑定 | 指定 fnSPACE 的 EF01/02/04/08 映射为空白；字符使用读到该位置时的显式字体定义 | `tests/test_mtef_extensions.py` 空白及错误字面体负例；`tests/test_mtef_fonts.py` 原始 7 对象及定义缺失负例 | 不删除任意私有区字符，不全局允许 Symbol/MT Extra 或任意自定义字体 |
| MTEF 记录消费 | COLOR 操作数与真实 prime 区分；已知 TeX 输入历史记录识别但不用其文本替换源树 | `tests/test_mtef_extensions.py` 原始对象及畸形字节 | 未知扩展、尾部未解析字节继续显式失败 |
| Word run 上下标 | 基于原文连续 run 和匹配括号合并 `(NO₃)₂`，保留子题号、上下方向及所有来源 | `tests/test_script_context.py` 原始 10 处、嵌套/括号不匹配/空白/OLE 边界 | 不跨空白、正文或嵌入对象猜基底 |
| OMML | 已列入允许结构的分式、上下标、根式、定界符、上下条件、横线与 eqArr；XML 子节点位置检查 | `tests/test_formulas.py` 正反例及真实对象 | 未知标签、属性、分数类型、缺失槽位保持 review；不以同题 OMML 替代 MathType 源 |
| 原始预览图片 | 校验 OLE 与预览绑定、源 SHA、真实格式、尺寸及现有字体编码限制 | `tests/test_formula_images.py`、`test_preview_binding.py`；libwmf/LibreOffice 独立探测 | “命令退出 0、PNG 非空”不等于字形正确；缺字结果不进入导出 |
| KaTeX 显示 | 已转换 LaTeX 的离线预览，带资源和严格模式 | 已有原生窗口及离线 HTML 校验；固定资源在包内 | 渲染器不解析 MTEF；不支持表达式明确显示源码，不能改写原公式来求显示成功 |

## 对当前项目的决定

1. **本次修复使用原始 MTEF 结构补齐常见模板，不硬编码题号，也不绕过所有字体门禁。** 源对象已有箭头方向、上下槽位和条件字符，应先完成确定性转换。公式图片是可靠的备选表示，不应掩盖可修复的结构解析缺口。
2. **优先引入开发端独立对照，而不是立即把整套 Java/Ruby/Office 资源塞进客户包。** 以 transpect/Ruby 和 mathtypejx 的 MTEF → MathML 路线对比方向、上下槽位、私有空白、矩阵和前置上下标。对照工具失败或输出不同须记录，不能自动择“看起来最好”的结果。
3. **是否更换主解析器由样本证据决定。** 候选需要输出全部原始对象的逐一状态、文本/结构保留率、未知节点行为、离线依赖和许可证清单；小样本通过再跑九份文档。安装成功、README 成功率、转换未抛异常都不是题意正确的证据。
4. **分发体积使用构建产物实测。** 当前包已有嵌入 Python；纯 Python 适配通常不需要另一个语言运行时，但“通常更小”是工程判断。Ruby/Java/Aspose 未构建方案不填写虚构 MB 数。LibreOffice 已记录本机约 414 MB，不据此推算目标平台包大小。所有方案均不以 Mac 结果承诺 Windows 可用。

本次查阅资料和源码没有将用户文档上传到任何外部服务。官方当前 MTEF 文档链接重定向登录页时，使用已注明来源的 [Design Science 原始 MTEF5 文档保存副本](https://rtf2latex2e.sourceforge.net/MTEF5.html) 与具体开源实现交叉核对，并明确旧文档的不完整处，不把登录失败说成格式没有定义。
