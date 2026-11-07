# Q62 原始 WMF 离线显示验证（2026-11-07）

> 当前显示与可选原图分发结果以 [离线显示验收](display-release.md) 为准；本文保留此前调查与阶段证据。

结论：三个剩余矩阵公式的原始 WMF 可以由 LibreOffice 离线重放，完整保留文字、上下标和双横线位置。此路径没有解释或猜测 MTEF `(1,129,129)`，没有 OCR、重新编写化学公式或改造源 WMF。当前仅为诊断验证，不是已经启用的生产降级路径。

## 输入与文字证据

源：`MathType公式化学填空2.docx` Q62，OLE166–168，对应 F000318–F000320。原始 WMF 和 SHA256/字体/字符字节记录保存在 `outputs/display-matrix-probe/source-records.json`。

| 对象 | WMF 文字字体与编码 | 视觉核对结果 |
|---|---|---|
| F000318 | Times New Roman ASCII；Symbol 0x44 / 0xAD | 2HgO、Δ、双横线、2Hg+O₂、↑ 均可见 |
| F000319 | Times New Roman ASCII；宋体 charset134 的 GB2312 B5E3 / C8BC | 3Fe+2O₂、点燃、双横线、Fe₃O₄ 均可见 |
| F000320 | 同上 | 4P+5O₂、点燃、双横线、2P₂O₅ 均可见 |

这三张 WMF 不引用 MT Extra。分开的 ASCII 字符串是各自定位的字符组，不能把记录直接串接成公式。

## 已执行的显示链路

`WMF → LibreOffice Draw → PNG`，LibreOffice 26.8.0.0.alpha0，本机 macOS ARM64。

先使用系统字体确认图元重放，再排除系统字体目录，使用独立 fontconfig 指向 LibreOffice 自带字体目录及项目 Noto CJK 目录。映射为：Times New Roman → Liberation Serif；Symbol → OpenSymbol；宋体/SimSun → Noto Sans CJK SC。没有全局安装字体，没有依赖 Office 或 MathType。

最终三个可复现结果：`outputs/display-matrix-probe/reproduced/F000318.png` 至 `F000320.png`，运行记录 `run.json`。`ofl-fonts/` 是先前同配置的三个已人工查看图片；目录名仅表示排除了系统字体，**不表示 OpenSymbol 也采用 OFL**。替代字体保留可辨认文字及源绘图坐标；未声称与原 Windows 字体像素相同。

复现入口：`scripts/probe_original_wmf.py --help`。脚本要求显式传入 runtime、其字体目录和 CJK 字体目录；独立临时 profile/cache，不覆盖已有输出，不更改源文件。当前 PNG 是整张 Draw 页面、公式较小；作为产品图片仍需确定高分辨率导出/裁切配置并再次核验。

## 体积、许可及候选取舍

| 方案 | 本次实际结果 | 分发影响 |
|---|---|---|
| LibreOffice 原始 WMF 重放 | 3/3 文字/符号/位置目视通过；无需矩阵解释 | 本机应用目录文件总计 427,279,695 bytes（约407.5MiB），另有16MB Noto CJK；不能为3个对象悄悄增加整个运行时 |
| emf-converter 4.28.0 | 原始 SVG 可正确解码 GB2312 点燃，但字体名显示为 ËÎÌå；原始 Symbol D/soft-hyphen 尚未正确显示；两个 CJK PNG 返回null | ESM 文件2,465,504 bytes，npm目录约10MB；Apache-2.0，纯JS，但 CLI 需JS运行时，未形成可靠可交付路径 |
| libwmf | 既有字体探针不能完整显示中文/符号 | 不能仅因进程成功就把结果当作可读图片 |

LibreOffice 产品主许可 MPL-2.0，包含其他许可组件，打包必须携带其完整 LICENSE/NOTICE 和满足相应源码义务；不能把整个应用简称为单一 OFL。Noto CJK 和现代 Liberation 字体为 OFL-1.1。OpenSymbol 属 LibreOffice 字体组件，独立抽取重新分发前仍应冻结该字体的许可证/来源证据。当前没有复制 LibreOffice 或系统字体到项目交付目录。

依据：[LibreOffice licenses](https://www.libreoffice.org/licenses/)、[Liberation upstream](https://github.com/liberationfonts/liberation-fonts)、[emf-converter upstream](https://github.com/ChristopherVR/emf-converter)，以及本机 LibreOffice `Contents/Resources/LICENSE` 与 npm 包 LICENSE。

推荐：保留该脚本作为确定有效的诊断/构建期转换工具；若选择产品期通用原始 WMF 重放，明确接受 LibreOffice 的运行时大小，再分别构建和验证平台便携包。若保持轻量包，则这3个对象继续标为未解决，或接受已经核验且按原始资源 SHA 绑定的离线预生成图片（这只覆盖固定样本，不能冒充通用解析器）。Windows 原生运行、客户环境启动和平台包均未验证。

## 可选运行时适配器（后续实现）

已增加 `src/qa_parser/libreoffice_preview.py`。仅当明确提供可选运行时才启用；冻结布局为 `_MEIPASS/libreoffice/LibreOfficeDev.app`，开发测试使用 `QA_PARSER_LIBREOFFICE_ROOT` 指向该 app。不搜索 PATH 或系统安装。接口 `preview_to_png(data: bytes) -> dict` 与原图片转换接口字段一致，附带字体、字符集、原始字节、解码文字的 `font_evidence`。

允许经过结构校验的原始 WMF 图元；Times New Roman 仅 ASCII，Symbol 仅已核验的 0x44/0xAD；宋体/SimSun charset134 使用严格 GBK 解码，并逐字核对项目字体 manifest 的 Unicode 范围、校验字体 SHA256。中文范围不绑定“点燃”样本字串。依据：[Microsoft 字符集134定义](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.inputlanguagechangedeventargs.charset?view=windowsdesktop-10.0)、[CP936 的 GBK 扩展](https://learn.microsoft.com/en-us/globalization/encoding/code-pages)。

输出直接以2448×3168像素从原始绘图导出，随后裁去空白边界、保留4像素边距，不重采样。三张适配器输出为360×93、402×107、381×107，已逐张目视核对 Δ/↑、中文、下标与线条位置。`tests/test_libreoffice_preview.py` 包括真实3份源 WMF、缺运行时、未知字体、未知 Symbol、非样本中文覆盖及缺字拦截；9项通过。Windows 支持仍未验证；当前运行时布局明确限定 Mac，不假装已有Windows便携支持。

OpenSymbol 官方说明明确指出 LibreOffice 内部通过 `unotools/source/misc/fontcvt.cxx` 的转换表适配其他 Symbol 字体；这解释了直接读取该系统符号字体的轻量候选失败，而 LibreOffice 能正确显示。见 [OpenSymbol upstream README](https://github.com/LibreOffice/core/blob/master/extras/source/truetype/symbol/README)。应随整个可选应用保留 LibreOffice 原 LICENSE/NOTICE，不能以 Noto OFL 代替所有字体的声明。
