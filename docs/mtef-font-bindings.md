# MTEF 字体绑定与原图兜底复查

2026-11-07 本轮检查原始九份 DOCX，发现此前 `typeface < 0` 或用户样式一律拒绝的规则过宽。

MTEF 中的 MTCode 标识字符，字体位置码是另一份字段。只要 MTCode 与字体、样式绑定都有明确记录，就不需要用 WMF 像素猜字。来源：[Wiris Font knowledge](https://docs.wiris.com/reference/font-knowledge)；[Design Science MTEF v5 原规范的保留副本](https://rtf2latex2e.sourceforge.net/MTEF5.html)。当前 Wiris SDK 的 MTEF v5 页面访问重定向登录，因此不能声称已经核对了该站当前在线正文。

## 七个实际对象

| 源文档及 OLE | Unicode 字符 | 原始字体/样式绑定 |
|---|---|---|
| 化学2 / 28 | 氢气氧气点燃水 | 显式字体 -1 → FONT_STYLE_DEF #1 → 宋体，plain |
| 化学填空2 / 170 | ↑、↑ | 显式字体 -1 → Symbol，plain |
| 化学填空2 / 232 | Δ | 显式字体 -1 → Malgun Gothic，plain |
| 物理 / 601 | A | USER2 #10 → Times New Roman，plain |
| 物理 / 654、655、656 | ∶ | 显式字体 -1 → Batang，plain |

`mtef_fonts.FontMetadataMixin` 保留原组件丢弃的 FONT_STYLE_DEF 和 EQN_PREFS 字体样式，按读取字符时已经存在的定义绑定，而不是事后猜测字体。

有限放行条件：已知普通字体、plain 样式、字符与 MTCode 一致、非私有区、受支持 ASCII/CJK/明确数学符号、无未处理装饰。`fnVECTOR`、未知字体、粗斜体用户样式、缺少定义、缺少 MTCode、私有区仍拒绝。`↑`、`Δ`、`∶` 显式转换成 LaTeX 命令；plain `A` 保持 `\mathrm{A}`。这不改变 WMF 字体审计。

## 原图渲染实际复测

原始 WMF 均从既有源对象绑定证据取出，仅在诊断目录运行，没有加入可信图片或客户包。目录：`outputs/arrow-validation/font-render-probe/`。

本机 libwmf 0.2.13 五个代表样本全部退出码为 0，但视觉检查显示：

- 化学2 第1题 F000001：原始“点燃”条件箭头被渲染为空白。
- 化学2 第13题 F000020：MT Extra 箭头部件变成 `uuuuur`，同时丢失中文。
- 化学2 第30题 F000038：只剩加号，中文及条件箭头丢失。
- 物理第89题 F000871：原本 `3∶2` 只剩 `3 2`，比例号丢失。

因此“命令成功、PNG 非空”仍不足以证明原图兜底安全；简单添加 Symbol/宋体白名单不能解决编码和字形对应问题。

随包已有 StandardSymbolsPS 字体，但 MT Extra 未提供。本机系统 Songti 字体存在不等于允许随应用再分发；并且单纯增加 CJK 字体仍未建立 libwmf 旧字节编码到 Unicode 的可靠映射。直接恢复有 MTCode 的原始结构能够避免这些字体依赖。

测试 `tests/test_mtef_fonts.py` 包含七个原始对象、定义顺序绑定、缺失定义、未知字体/样式、私有区和装饰拒绝，共 23 项。

### 第二条离线路径：LibreOffice 实测

本机已有的 `LibreOfficeDev 26.8.0.0.alpha0`（`2c87e51eeaa2b413ff4ae097b2705eea1995d8e5`）可直接以 headless Draw 导入五个原始 WMF 并导出 PNG，五次均退出 0。该本地运行时目录约 414 MB，本轮没有安装、下载或打包它。

实际查看 PNG：比例 `3∶2` 得以显示；化学第38题的高锰酸钾条件式也出现可辨内容。但第1题“点燃”、第13题及第30题中文仍为缺字方框，MT Extra 部分仍未建立可靠字形对应。输出还带完整空白画布，若采用需额外处理边界、尺度、字体供应及版本固定。

因此 LibreOffice 是比现有 libwmf 更有潜力的可选验证路径，但当前配置不能作为通用自动兜底。它增加约 414 MB 的本机依赖，且这是开发版；目标 Windows 原生运行、字体许可和离线分发均未验证。本轮优先恢复 MTEF 明确结构，不将该运行时加入客户包，也没有使用外部识别服务。

探测日志：`outputs/arrow-validation/font-render-probe/probe.json`、`libreoffice-probe.json`。诊断 PNG 不是已通过的导出资源。
