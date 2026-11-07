# 原始公式预览转 PNG 的验证边界

本模块只渲染 DOCX 中与公式对象绑定的原始预览资源，不使用失败的 LaTeX 猜测重绘。源对象、ShapeID 绑定、预览哈希和题目关联由核心层保留。

## 采用的依赖与接口

- 栅格预览：Pillow 12.3.0，验证真实文件格式、解码、尺寸、帧数和可见像素，再编码为 PNG。Pillow 12.3.0 包元数据标注 MIT-CMU 许可证；当前使用已安装 Python 环境，不增加 Office 依赖。
- WMF：独立小工具 `wmf2gd`（libwmf 0.2.13），将原始 WMF 渲染为 PNG。当前 Mac 开发机通过 Homebrew 安装，安装体积约 4 MB，不含已有动态依赖。
- 适配器仅做有限的元数据、字体、字符集与记录审计，不实现新的 WMF 绘制器。
- `preview_to_png(data: bytes, extension: str)` 返回 `status、png、reason、engine、width、height`。失败时 `png=None`、宽高为 0，不抛出异常中断整个任务。
- 运行时从 `QA_PARSER_WMF2GD` 或 PATH 定位工具；缺失即返回明确失败，不联网下载，不尝试安装任何软件。

当前 Homebrew **实际安装的 0.2.13 配方**标注 `LGPL-2.0-or-later AND GPL-2.0-or-later AND GD`。不要用官网当前较新版本的许可证摘要代替实际版本记录。动态依赖包括 libwmf/libwmflite、FreeType、libpng、jpeg-turbo，以及 macOS 的 expat/zlib。libwmf 随带的 Nimbus/StandardSymbols 字体也属于后续再分发审查范围。本次只完成开发机验证，未制作包含这些库和字体的客户分发包。

组件来源：[libwmf 上游](https://github.com/caolanm/libwmf)、[Homebrew 配方](https://formulae.brew.sh/formula/libwmf)、[Pillow 文档](https://pillow.readthedocs.io/en/stable/)。

## 真实样本结果

验证输入为 `outputs/mac-acceptance/*/task/task.json` 中 341 个 `status=review` 且存在原始预览的 MathType 公式。

**直接调用 libwmf：341 个全部返回退出码 0，但不等于转换可靠。** 视觉检查发现：

- 化学反应条件中的中文可以完全消失。
- Symbol 字体中的 Ω 等字符可以丢失。
- MT Extra/特殊字体箭头可能变成无关的拉丁字符，或只剩加号。
- 某些输出完全空白；另一些虽非空，内容仍不完整。

因此不能以“进程成功 + PNG 非空”作为自动通过条件。

保守审计后的实际结果：

| 结果 | 预览数量 |
|---|---:|
| 已转换为 PNG | 41 |
| 拒绝自动转换，保留原始证据 | 300 |

拒绝类别按首个命中的原因统计：Symbol 字体 153、宋体 119、未验证字符集 161 共 24、Batang 3、MT Extra 1。一个预览可能还包含其他未报告的字体问题。

41 个成功输出均已查看缩略图，包含物理量、普通上下标、斜分式以及拉丁化学式/条件等号。此视觉检查不代表任意 WMF 的普遍保真认证，也不能替代源公式绑定检查。完整机器报告和可信 PNG 位于：

`outputs/formula-preview-validation/report.json`

示例：

- `MathType公式化学填空2-F000183.png`：斜分式。
- `MathType公式化学填空2-F000328.png`：H₂SiO₃。
- `MathType公式化学填空2-F000393.png`：MnO₂ 条件等号。
- `MathType公式物理-F000050.png`：100 cm²。

原始不可靠渲染仅作为诊断保留在 `/tmp/qa-preview-probe/`，不放入可信输出目录，也不作为题目导出资源。

## 自动通过条件

WMF 需要同时满足：

1. 头部、长度、对象表、记录边界及 EOF 完整；只接受样本验证过的记录类型，Escape 仅允许元数据注释。
2. 字体必须显式创建并选中；仅接受已列明的拉丁字体和字符集 0/1。不能靠初始默认字体猜测。
3. 所有绘制文字必须为可打印 ASCII。Symbol、MT Extra、中文、自定义字体、非 ASCII 字形、未验证字符集均拒绝。
4. 工具没有报告错误或警告，输出 PNG 能真实解码，尺寸受限，并存在可见、非单色内容。

已实现超时、输入大小、像素数量限制；使用临时目录和参数列表调用工具，不通过 shell 拼接命令。不支持的 EMF 及其他格式明确失败。

## 测试与后续

运行：

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/test_formula_images.py -q
```

当前 16 项测试通过，覆盖 PNG 像素保持、JPEG 真正转码、空白/损坏/扩展名伪装、缺工具、进程零退出但内容空白、字体/字符集静默丢失风险、截断 WMF，以及真实 MathType 预览。真实 WMF 测试在未安装可选工具的环境中明确 skip。

本轮交付可信子集和明确隔离，没有继续扩大到整套办公软件或另一套渲染平台。RTFJS/WMFJS 可作为后续依赖包候选，但尚未验证其字符集映射、字体匹配及离线 SVG 渲染，因此没有接入。剩余 300 个不允许以当前 libwmf 输出自动替代；原始证据和失败原因继续保留。

这只是 Mac 本地原图兜底验证。Windows 运行、工具/字体便携打包、再分发和真实 APP 展示均未验证。
