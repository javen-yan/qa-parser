# 877 / 23 中间结果固定快照

本记录固定来自 `outputs/arrow-validation/intermediate-batch`，用于说明当时 900 题中已导出 877 题、仍未导出 23 题的真实原因。后续代码和覆盖率变化不改写本快照。

未通过题目共涉及 **43 个公式对象**。这里列出的是转换器的支持缺口，不代表原始题目或公式不可读。

| 转换缺口 | 涉及题数 | 涉及公式对象数 | 文档及原题号 |
|---|---:|---:|---|
| 显式字体或用户样式字符映射 | 5 | 7 | MathType公式化学2.docx：30；MathType公式化学填空2.docx：64,81；MathType公式物理.docx：81,89 |
| 上下限模板 selector=23 variation=32 | 6 | 6 | MathType公式化学2.docx：30；MathType公式化学填空.docx：24,52,72；MathType公式化学填空2.docx：86,91 |
| MTCode 私有区字符（EF01） | 5 | 13 | MathType公式化学3.docx：15,38,54；MathType公式物理.docx：1；MathType公式物理2.docx：84 |
| MTCode 私有区字符（EF02） | 3 | 3 | MathType公式化学3.docx：43；MathType公式物理.docx：6；MathType公式物理2.docx：20 |
| 多行/矩阵对齐或槽位结构 | 2 | 5 | MathType公式化学填空2.docx：41,62 |
| MTCode 私有区字符（EF08） | 1 | 1 | MathType公式化学填空2.docx：93 |
| MTCode 私有区字符（EF04） | 3 | 7 | MathType公式物理.docx：6,81,98 |
| 前置上标 selector=28 variation=1 | 1 | 1 | MathType公式物理2.docx：6 |

**这些类别存在交集，题数不能相加。** 例如同一题可以同时含私有区空白字符和显式字体字符，也可以同时含显式字体与上下限模板。每个对象保留完整分号拆分原因，原始定位和证据 SHA 见 JSON。

## 当时的图片兜底阻断

| 首个预览阻断原因 | 对象数 |
|---|---:|
| ValueError: WMF uses an unverified font for text: non-ASCII font bytes cbcecce5 | 6 |
| ValueError: WMF uses an unverified font for text: symbol | 30 |
| ValueError: WMF uses an unverified font charset: 161 | 4 |
| ValueError: WMF uses an unverified font for text: batang | 3 |

上表是 WMF 检查遇到的首个阻断，不能据此断言该对象只有一种字体问题，也不能将其等同于公式解析失败的全部原因。JSON 同时保留各对象的完整模板、私有区 MTCode、字体编号和多行结构清单；未采用有失真的图片替代源内容。

## 九份文档

| 文档 | 通过 | 未通过 |
|---|---:|---:|
| MathType公式化学.docx | 100 | 0 |
| MathType公式化学2.docx | 99 | 1 |
| MathType公式化学3.docx | 96 | 4 |
| MathType公式化学填空.docx | 97 | 3 |
| MathType公式化学填空2.docx | 93 | 7 |
| MathType公式物理.docx | 95 | 5 |
| MathType公式物理2.docx | 97 | 3 |
| 微软公式化学.docx | 100 | 0 |
| 微软公式物理.docx | 100 | 0 |

机器可读明细：[remaining-877.json](../outputs/arrow-validation/remaining-877.json)。本快照仅说明阶段状态，不构成最终验收结论。
