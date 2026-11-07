# 公式本地验证记录

验证环境：macOS ARM64，Python 3.13，omml2latex 0.1.1、olefile 0.47；2026-11-07。

## 全样本结构转换覆盖

下表统计的是正文中出现的公式对象，不是题目数。`converted` 是严格结构适配接受，不是全部公式人工/视觉验真。

| 文档 | 公式总数 | converted | review |
|---|---:|---:|---:|
| MathType公式化学.docx | 44 | 40 | 4 |
| MathType公式化学2.docx | 93 | 40 | 53 |
| MathType公式化学3.docx | 504 | 483 | 21 |
| MathType公式化学填空.docx | 148 | 100 | 48 |
| MathType公式化学填空2.docx | 277 | 136 | 141 |
| MathType公式物理.docx | 724 | 661 | 63 |
| MathType公式物理2.docx | 454 | 443 | 11 |
| 微软公式化学.docx | 93 | 67 | 26 |
| 微软公式物理.docx | 504 | 500 | 4 |

## 对应文档同序对照

按正文 XML 顺序和 OLE 关系定位配对；两组对象数分别相同。为定位差异，临时去掉字体包装命令、空白及花括号，并统一 to/rightarrow。该规范化可能隐藏分组差异，只用于筛选，不能证明数学等价；原始结果全部保留。

| 文档对 | 对象数 | 双方 converted | 规范化相同 | 待核对差异 |
|---|---:|---:|---:|---:|
| MathType公式化学3.docx ↔ 微软公式物理.docx | 504 | 479 | 473 | 6 |
| MathType公式化学2.docx ↔ 微软公式化学.docx | 93 | 40 | 37 | 3 |

### MathType公式化学3.docx ↔ 微软公式物理.docx

- 公式序号 41：MTEF `R_L=\frac{U_L}{I}=\frac{U_L}{U_R}·R=\frac{2.5V}{U_R}·R`；OMML `{R}_{L}=\frac{{U}_{L}}{I}=\frac{{U}_{L}}{{U}_{R}} \cdot R=\frac{2.5V}{{U}_{R}} \cdot R`。
- 公式序号 44：MTEF `R_L=\frac{U_L}{U_R}·R=\frac{U_L}{U_总-U_L}·R=\frac{2.5V}{U_总-2.5V}·R`；OMML `{R}_{L}=\frac{{U}_{L}}{{U}_{R}} \cdot R=\frac{{U}_{L}}{{U}_{总}-{U}_{L}} \cdot R=\frac{2.5V}{{U}_{总}-2.5V} \cdot R`。
- 公式序号 67：MTEF `\frac{1}{R_并}=\frac{1}{R_1}+\frac{1}{R_2}+……`；OMML `\frac{1}{{R}_{\text{并}}}=\frac{1}{{R}_{1}}+\frac{1}{{R}_{2}}+ \ldots  \ldots`。
- 公式序号 331：MTEF `U={I'}R_x`；OMML `U={I}^{′}{R}_{\text{x}}`。
- 公式序号 458：MTEF `U=I_1\left(R_0+R_x\right)`；OMML `U={I}_{1}({R}_{0}+{R}_{x})`。
- 公式序号 503：MTEF `U_L=1V`；OMML `{U}_{\text{L}}^{}=1\text{V}`。
### MathType公式化学2.docx ↔ 微软公式化学.docx

- 公式序号 34：MTEF `H_2CO_3=H_2O+CO_2↑`；OMML `{\text{H}}_{\text{2}}{\text{CO}}_{\text{3}}{\text{=H}}_{\text{2}}{\text{O+CO}}_{\text{2}} \uparrow`。
- 公式序号 36：MTEF `CaCO_3+2HCl=CaCl_2+CO_2↑+H_2O`；OMML `{\text{CaCO}}_{\text{3}}{\text{+2HCl=CaCl}}_{\text{2}}{\text{+CO}}_{\text{2}} \uparrow {\text{+H}}_{\text{2}}\text{O}`。
- 公式序号 65：MTEF `2H_2O_2=2H_2O+O_2↑`；OMML `{\text{2H}}_{\text{2}}{\text{O}}_{\text{2}}{\text{=2H}}_{\text{2}}{\text{O+O}}_{\text{2}}\mathrm{~\uparrow~}`。

## 代表性源结构核对与发现

- 物理文档对的第 1、2、3 个公式：R 的下标 0/x，以及 Rx=Ux/Ix。MTEF 对象树与 OMML 的 sub、num、den 顺序一致；两条输出保留变量、分式分子分母。已做固定回归断言。
- 物理第 7 个公式包含 R=U/I=3V/3A=1Ω，核对数字、单位、除法与等号顺序；两个组件只在字体包装和空白上不同。
- 化学第 5 个公式 CO₂；第 19 个公式 22+6+5+4=9+6+19+x：核对下标、数字和运算符顺序。化学第 34、36、65 个公式的差异是原始 Unicode ↑ 与 LaTeX `\uparrow` 表达差异；气体标记未丢失。
- 物理对照差异中的 U=I′Rx 是 prime 的表达差异，U_L=1V 是源 OMML 的空上标；乘号和省略号也有 Unicode/命令表达差异。以上为源结构与文本语义检查，尚未做原 Word/目标 APP 的图形渲染验收。
- 发现 OMML 包把原文 `0~0.6A` 中的 `~` 原样输出，LaTeX 中会变成间隔而丢失范围符号。已新增严格 review 与回归测试，不将这两条视为转换成功。
- 发现 OMML 非默认分数类型会被组件转换成统一 frac，已对 lin/skw/noBar 标记 review。现有物理样本有两条因此进入复核。
- MTEF 原模块对未实现模板会拼接槽位，且部分私有码可能丢弃。全部经过适配层拦截，化学条件箭头/多行等保留为复核，不为了通过率放行。

## 未完成边界

未完成所有公式人工核验、Word 页面渲染比对、目标 APP 渲染或 Windows 运行。剩余 review 项是当前已识别的转换覆盖缺口，不是用户文档数据错误。输入资源及源位置应随任务保存，供人工修正与后续小范围适配。
