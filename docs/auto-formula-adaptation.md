# 自动公式转换的 OMML 薄适配

2026-11-07，macOS ARM64、Python 3.13、omml2latex 0.1.1。此次仅修改公式适配和测试，不新增运行依赖，不变更题目规则、图片处理或导出流程。

## 本次范围与实际结果

用户最新要求为自动流程：可靠 LaTeX 优先，无法转换时由资源层尝试原图兜底，不要求用户逐题确认。公式适配 API 保持兼容：`converted` 返回 LaTeX；`review` 表示未能可靠转换、应尝试原图兜底，不代表要求弹出人工交互。没有可靠 LaTeX 或可用原图时仍应明确失败，不猜测。

| 样本文档 | 公式数 | 适配前 converted / review | 适配后 converted / review |
|---|---:|---:|---:|
| 微软公式物理.docx | 504 | 500 / 4 | 504 / 0 |
| 微软公式化学.docx | 93 | 67 / 26 | 93 / 0 |
| 合计 | 597 | 567 / 30 | 597 / 0 |

MathType MTEF 本次未改转换范围：2244 个公式仍为 1903 converted、341 review。后者由调用方使用原公式预览图兜底；本适配模块不执行图像转换。

以上是实际运行的结构转换结果，不是原 Word 页面的像素一致性证明，也不是目标 APP 或 Windows 验收。

## 三项小范围适配

### 1. 线性分式 lin

原组件会将 `m:type="lin"` 统一转成 `\frac`。现由适配层只处理 lin，保留横向斜线：

- 样本公式 `I/A` 输出 `{I}/{\text{A}}`，`U/V` 同理。
- 复合分子、分母加可见括号以保留操作优先级，例如 `(a+b)/(c+d)`。
- 递归子表达式仍调用原包公开 API，采用局部占位替换，不修改依赖包，不使用全局 monkey-patch。
- `skw`、`noBar` 和未知分式类型仍不自动放行。

### 2. ASCII tilde

原组件直接保留 `~`，在 LaTeX 中会变成空格，丢失源文可见符号。现在数学模式中的 ASCII `~` 显式输出 `\sim`，例如 `0\sim 0.6\text{A}`。普通文字模式 `m:nor` 内的 ASCII tilde 仍不猜测。

### 3. eqArr 与 bar

复用原包已有的多行数组和横线转换；适配层增加节点位置、行存在性、横线 `pos` 枚举检查。保留行序、空行和嵌套横线，不删除条件文字。

现有化学样本中的十五个公式包含“两个未指定 pos 的 bar 包裹条件文字，再接空行”。官方 OOXML 定义 barPr 省略时横线默认位于顶部，因此按源结构保留双 `\overline`，**没有**根据化学常识改写成等号、下横线或反应箭头。另十一条是数组内的 limLow 条件及箭头，保留其上下结构。

这一点很重要：若源文的 OMML 本身并非作者期望的化学版式，转换工具不进行化学内容纠正。

## 本地验证

```sh
.venv/bin/python -m pytest -q tests/test_formulas.py
```

本次结果：20 passed。新增或更新检查包括线性分式优先级、真实 I/A 与范围符、化学双bar不伪造箭头、数组行顺序、未知横线位置/数组属性仍拒绝。另重新遍历两份 Word 正文中的全部 597 个 OMML 节点，得到上表结果。

## 仍未支持与兜底边界

- 未验证的 OMML 节点、私有字形、LaTeX 保留字符、未知属性值和 malformed XML 仍返回明确原因。
- 数组的额外未验证属性（例如复杂对齐/间距结构）不因加入 eqArr 就全部放行。
- MTEF 的未知模板、私有码、扩展记录或复杂布局本次保持原有严格判断。
- 本次没有引入独立 OMML 原生渲染器。把未知 OMML 先猜成 LaTeX 再渲染 PNG，不算可信原图兜底；缺少原始预览资源时，不能伪装成已有截图。
- 原图兜底能力由图像模块负责，包括 WMF 解码与 PNG 输出；该模块的实际结果不包含在本报告中。

## 依据

- omml2latex 公开 API及格式说明：https://pypi.org/project/omml2latex/0.1.1/
- Microsoft OOXML BarProperties（省略时默认 top）：https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.math.barproperties?view=openxml-3.0.1
- Microsoft OOXML Bar：https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.math.bar?view=openxml-3.0.1

## 第二次有界适配：MTEF 条件横线堆叠

本节为后续实际执行结果，更新上文“MathType 本次未改转换范围”的历史快照；其他未支持结构继续保留图像兜底/失败状态。

### 准入依据与实现范围

只复用既有模块的二进制解析和 AST，不新增完整解析器。适配层保留模块原本丢弃的矩阵对齐、行列分隔线和模板选项字节，再验证下列严格子集：

- `tmUBAR=12` / `tmOBAR=13`，variation 仅 0（单线）或 1（双线），一个槽位，模板选项为 0。分别输出一层/两层 `\underline` 或 `\overline`，修正上游直接忽略双线位的问题。
- MATRIX 仅 2 行 1 列且恰有两个单元，对齐元数据恰为 `(1,1,1)`，分隔线字节为全零；首行必须是一个上述横线模板，第二行必须为空。输出保留左对齐、首行条件和显式空行的数组。
- PILE 仅 2 行、水平居中、中心行基线对齐，首行一个横线模板、尾行空。其他 PILE 不开放。
- 所有子节点继续经过原来的字体/Unicode、未知模板及扩展检查。未知对齐标志（样本有 129）、分隔线、多列、多余单元以及非空尾行不会被这个子集放行。

格式依据：原厂 MTEF v5 文档的历史镜像中，selector 12/13 分别为下/上横线，variation 位 1 表示双线；矩阵元数据描述行列顺序、对齐和分隔线。

来源：https://rtf2latex2e.sourceforge.net/MTEF5.html （原厂历史格式文档镜像；当前 Wiris 原链接不可访问）。没有将该文档或其他项目的完整实现打包进产品。

### 实际覆盖

| 文档 | 原 converted | 新 converted | 新 review |
|---|---:|---:|---:|
| MathType公式化学.docx | 40 | 44 | 0 |
| MathType公式化学2.docx | 40 | 53 | 40 |
| MathType公式化学3.docx | 483 | 483 | 21 |
| MathType公式化学填空.docx | 100 | 137 | 11 |
| MathType公式化学填空2.docx | 136 | 247 | 30 |
| MathType公式物理.docx | 661 | 661 | 63 |
| MathType公式物理2.docx | 443 | 443 | 11 |
| 合计 | 1903 | 2068 | 176 |

新增 165 个公式可从自身源 AST 转换；总数仍为 2244。OMML 仍为 597/597 结构转换成功，未使用另一份文档填补任何当前输入。

### 同源参考与验证结果

真实样本 `MathType公式化学2.docx` 的第 4 个公式：AST 是 `4P+5O₂`、2×1 矩阵、`tmUBAR variation=1` 包裹“点燃”、空行、`2P₂O₅`。输出为：

```latex
4P+5O_2\begin{array}{l}\underline{\underline{点燃}} \\ {}\end{array}2P_2O_5
```

相应 OMML 文档的该公式却存储两个默认顶部的 `bar`。两份输入的横线方向存在源结构差异，因此各自忠实解析：MTEF 输出双下横线，OMML 输出双上横线；不把 OMML 结果当答案覆盖 MTEF，也不根据化学常识补等号或箭头。

新增固定测试覆盖这个真实公式，以及矩阵未知对齐/分隔线/额外单元的拒绝，和四种单/双、上/下横线的方向与条数。`tests/test_formulas.py` 当前 **26 passed**。这证明有限结构和生成文本的对应，不宣称原 Word 的字号、微小位移或像素版式完全一致。

尚未支持的 176 个 MTEF 公式仍包括未知字体/私有码、箭头和其他复杂布局。此次有界检查结束，不扩大到未知字符映射或泛化矩阵。
