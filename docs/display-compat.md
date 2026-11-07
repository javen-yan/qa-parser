# 离线公式显示兼容（2026-11-07）

本轮将“可阅读显示”与“结构导出”分别验证。此文记录显示适配代码及数据检查，最终字体、原生窗口、图片及客户包验证以主交付报告为准。

## 已查明的 250 处源码回退

固定输入为 `outputs/arrow-validation/all-latex.json`：4709 个已转换公式。此前 KaTeX 0.19.1 `strict: "error"` 接受4459个、拒绝250个。对所有警告逐一记录后，未发现这250个存在不支持宏或 LaTeX 语法错误；阻断项都是字符/字体度量兼容诊断。

互斥分类如下：

| 类别 | 公式对象数 |
|---|---:|
| 仅中文、全角文字的 math-mode 提示 | 174 |
| 仅圈号、Roman 数字、比例号、prime、小减号、零宽空格等命名 Unicode 提示 | 73 |
| 两种提示并存 | 3 |
| 合计 | 250 |

同一公式可以触发多个字符及度量提示。共59种兼容字符；原格式来源为 OMML79、Word run116、MathType55。明细见 `outputs/display-validation/katex-compat.json`、`unicode-inventory.json`。

## 使用现有成熟显示引擎

继续复用随包固定的 KaTeX 0.19.1，没有为这批字符额外加入第二数学渲染器或 Java/Node 客户运行时。`find-docs` 的 Context7 CLI 因包源域名解析失败未能运行；随后直接核查 KaTeX 官方文档及当前随包实现，没有用训练记忆代替 API 确认。

[KaTeX options](https://katex.org/docs/options) 区分 LaTeX 严格性提示与语法错误，提供按错误代码选择处理方式的 `strict` 回调。[Unicode 支持说明](https://katex.org/docs/supported#letters-and-unicode) 明确提供中文等文字的原生显示与 CSS 字体回退，同时说明非内置字符的度量近似限制。不能仅因为排版没有抛异常就认定原文保真。

实现位于 `src/qa_parser/web/math-preview.js`，UI 和离线 HTML 调用同一模块：

- `QaMathPreview.render(latex, element)`：构造经过审查的 KaTeX HTML，并写入目标节点。
- `QaMathPreview.renderToString(latex)`：同一策略的离线/开发接口。
- `QaMathPreview.assess(latex)`：返回是否支持、完整原文、批准的逐字符警告或错误。

## 处理规则与保真边界

没有正则改写 LaTeX，没有把圈号换普通数字、全角符号换半角，也没有删除零宽空格。原始字符串、Unicode 码点、TeX结构及 MathML annotation 保持原样，使用 KaTeX 自身的 Unicode 文本路径。

只允许这几类诊断：

1. `unicodeTextInMathMode`：明确的中日韩统一/兼容汉字、CJK标点与全角文本范围。
2. `unknownSymbol`：明确命名的 Unicode 圈号1–20、RomanⅠ–Ⅻ、比例号、prime、摄氏、小减号、零宽空格。不能因此允许私有区或任意未知字符。
3. `symbolNotInFont`：严格匹配当前固定版本诊断格式，只接受上述字符在普通 `Main-Regular`、text模式下的缺度量提示。显式改变字体样式的内容另作审查。

发现 KaTeX 对 `\mathbf{①}` 等未知字符可以静默采用普通文字，本模块因此增加固定版本解析树检查：借助只读 `__parse` 审查宏展开后的 font/text 节点，明确粗体、斜体、黑板体等 Unicode 字体变化尚无对应验证时拒绝普通字体替换。此接口是 KaTeX 私有 API，所以 Unicode 路径锁定0.19.1，升级时必须重跑正反例；它不被当作跨版本稳定接口。

未知宏、错误语法、其他严格性警告、私有码、emoji和超长输入继续失败。外链/图片/HTML命令通过 `trust` 回调拒绝，包括藏在自定义宏中的命令；每次新建宏环境，防止跨题状态泄漏。模块未改变 DOCX/MTEF 原图的字体与字符集门禁。

## 字体与视觉验收要求

KaTeX 自带字体不覆盖全部中文/圈号等字符，必须随包提供具有明确再分发许可且包含这些码点的字体，不能依靠开发机字体碰巧存在。集成要求设置：

```css
.katex { font-family: KaTeX_Main, "QA Formula Unicode", serif; }
.katex .cjk_fallback { font-family: "QA Formula Unicode"; }
```

圈号等普通 `.mord` 沿用根字体回退；公式变量仍使用 KaTeX 自有 mathnormal 字体。实际使用的字体文件、cmap验证、许可、包大小及内嵌路径由主线程统一记录。

这条路径的字符来自源 Unicode，不是 OCR 或重画猜测。但字形可用与垂直度量仍需验证：原生窗口应核对中文上下条件、中文下标、圈号/全角符号、比例号、prime、Roman 数字和零宽空格；检查根式、分式、矩阵等组合中是否裁切、遮挡或串行。字号/字体度量规范化不等于 Word 逐像素复刻。

## 已完成的开发检查

- `node tests/test_math_preview.js`：源码码点保持、既有严格公式输出不变、宏/HTML/私有码负例、显式Unicode字体变化拒绝、跨公式宏状态隔离，全部通过。
- `node scripts/check_math_display.js INPUT.json NEW_REPORT.json`：使用同一共享模块重放全量4709公式。当前4709均可构造，4459不需要兼容路径，250按明确字符规则处理；原始源串全不变。
- 逐公式结果：`outputs/display-validation/replay-all-4709.json`。该脚本明确不声称已验证浏览器字体、裁切、源 Word 保真或客户 APP。

对目标系统：如果 Excel 仍保存原 `$$...$$`，本模块只保证本工具/离线阅读视图采用了正确显示策略，不能据此宣称目标 APP 已支持。若采用已核验显示结果的 PNG 交付，则需将字体及渲染器证据、图片与源位置关系和图片引用一并纳入导出验收。客户无需安装或配置字体，不参与逐题修复。

## 随包字体覆盖门禁（v2）

`font-coverage.js` 提供浏览器全局 `QaFormulaFont`，必须在 `math-preview.js` 前加载；Node 直接读取同一 `vendor/fonts/manifest.json`。它记录固定 Noto Sans CJK SC 字体的 SHA-256、字节数及实际 cmap 区间。字符需同时满足上述语义白名单和 cmap 覆盖，不能因为属于 CJK 区块便接受未分配或缺字码点。解析树也检查 `\text{...}` 等不触发数学模式警告的 Unicode 文本。没有覆盖信息时，普通数学仍可显示，兼容字符拒绝假成功。

U+200B 是唯一明确例外：它是无墨迹的零宽分隔符，无需字体 cmap 图形，保留原始字符和输出，不替换、不删除。浏览器验收应检查其没有可见方框或额外宽度。

加入浏览器/Node manifest 一致性、缺字 U+9FFF、缺少 manifest、文本模式缺字及零宽例外测试。`outputs/display-validation/replay-all-4709-font-covered.json` 重放结果仍为4709可构造、250兼容、0失败。覆盖元数据不证明字体已实际加载；集成方仍须等待 `document.fonts.ready`，检查字体加载状态及实际布局。字体与公式组件均来自本地文件，客户端无需安装字体。
