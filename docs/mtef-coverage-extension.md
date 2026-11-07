# MathType 剩余结构覆盖扩展（2026-11-07）

本次保留固定 `formula-flow/mtef` 组件，新增有限适配层 `mtef_extensions.py`；不复制整个转换器，不新增 Ruby/Java/Office/MathType 运行依赖。只使用源 MTEF 树，缓存 TeX 不参与最终表达式生成。该记录说明结构证据，视觉验收与九份文档最终数量以本轮主报告为准。

## 已定位并修复

- **装饰列表的颜色记录被误读**：原组件在 CHAR 的装饰列表中，把 `COLOR(15), 0` 误当成“装饰 0”。实际随后还有真实 `EMBELL(6)` prime/bar。薄适配按记录类型读取，跳过默认颜色索引 0，保留真实装饰；未知记录、非默认颜色仍报错。没有将装饰 0 添加到允许表。[Design Science MTEF5：Object lists、COLOR、EMBELL](https://rtf2latex2e.sourceforge.net/MTEF5.html)。
- **TeX 输入历史扩展**：只识别记录 102 的 `TeX Input Language` 名称、单一 NUL 终止内容。它是历史输入而非当前排版结构。解析仍完全依赖完整 MTEF 树；未知名称、缺终止符或额外字段继续拦截。已有固定组件对该字段的读取也作为实物交叉证据。
- **向量模板 31/6**：保留右向及“箭头位于正文下方”两个属性。`under/over` 与 `left/right` 分开处理，核对伸缩箭头字符 U+20D6/U+20D7 与方向一致；不套用组件中的向量括号启发式。
- **矩阵/多行**：按原行列保留全部内容、空行和空格子，只支持已明确的对齐值和无分隔线布局。允许经过递归审查的 PILE 作为矩阵单元，和 [transpect matrix.xsl](https://github.com/transpect/mathtype-extension/blob/master/xsl/transform/matrix.xsl) 一致。输出为普通 LaTeX array；这是布局规范化，不保证与 MathType 字体度量及基线逐像素一致。
- **上下限模板 23/32**：旧规范位标记与维护实现不一致。核对 [维护中的 MTEF5 Ruby 解析器](https://github.com/sbulka/mathtype/blob/master/lib/records5/tmpl.rb) 后采用 `0x10=lower / 0x20=upper`；[transpect lim.xsl](https://github.com/transpect/mathtype-extension/blob/master/xsl/transform/lim.xsl) 确认 main、lower、upper 顺序。使用 `overset/underset` 保留实际主内容：既能表示箭头上方条件，也能表示 Mg 上方 +2 的化合价；不凭模板名称插入 `lim`。
- **前置上标 28/1**：只在所在 LINE 后面有明确字母数字字符时支持。输出空基底前置上标，保持下一字符为基底。例如原树 `Q=I, preSUP(2), R,t` 输出 `Q=I{}^{2}Rt`；不根据物理常识改成 `I^2`。
- **四个明确的间距 MTCode**：仅限 fnSPACE=24 且无装饰的 EF01、EF02、EF04、EF08。[原作者字符映射表](https://github.com/jure/mathtype_to_mathml/blob/master/lib/xsl/char.xsl) 将其命名为零宽、细间距、粗间距、1 点间距。采用 `hspace{0pt}`、细/粗 LaTeX 数学间距和 `hspace{1pt}`。这是间距度量规范化，绝不能把 EF04/EF08 依照原组件的猜测映射为竖线，也没有放开其他私有码。

## 实际仍未覆盖的结构

在基线 136 个被隔离的 MathType 对象中，本次结构适配结合主线程箭头与 Unicode 字符修复后，133 个可转换，3 个仍含矩阵原始对齐字节 `(1,129,129)`。三者均在 `MathType公式化学填空2.docx` 的 F000319/F000320/F000321，均是化学式内的两行条件布局。这是对象编号审计信息，不用于程序分支。

已查询旧 SDK MTEF5 规范、维护中的 Ruby MTEF5 解析器和 transpect 矩阵转换。规范列出的对齐值不包括 129；Ruby 实现也按已知枚举解析，无法证明高位含义；没有直接清掉 0x80。后续可从更新 SDK/可验证渲染器明确该字段，或经可再分发字体与字符映射验证后使用原对象渲染。当前继续保存证据并报告确切字节，不把它描述为题目内容不可读。

## 开发验证

`tests/test_mtef_extensions.py` 包含 22 项检查：真实 COLOR+prime、历史 TeX 独立于结构、箭头方向/上下位置、非空下行、行列及空单元、嵌套 PILE、化合价/光照条件、前置上标及无基底拒绝、私有码错误 typeface/装饰拒绝。实际运行全部通过。

参考源码快照位于 `outputs/arrow-validation/component-evidence/`，仅用于开发审计，不作为运行时或客户交付依赖。

## 剩余三个对象的候选组件实测

另行下载并本地运行 [uwjiaoxing/mathtype2omml，feat/desktop-gui 分支](https://github.com/uwjiaoxing/mathtype2omml/tree/feat/desktop-gui) 的独立 MTEF→MathML 接口，直接输入原始 `oleObject166.bin`、`167.bin`、`168.bin`，没有调用 Office XSL，没有上传文档。三者均能生成含两行一列、空第二行、条件下双横线及完整化学式的 MathML，结果及原对象 SHA 见 `outputs/arrow-validation/component-evidence/candidate-matrix-probe.json`。

但检查其 `src/mathtypejx/mtef/records5.py` 第 277–279 行发现：它把字节 129 读成有符号 -127，随后用枚举查找失败时的默认 `center` / `center_baseline` 代替，**并未解释 0x80 高位**。因此“该组件输出了公式”不能证明原对齐含义已经支持。本轮没有照搬这一静默默认行为。这次实测提供了可读内容和结构的独立交叉证据，但不足以替代布局字段验证；最终继续仅隔离确实尚未覆盖的源题。
