# 离线公式显示与 Mac 交付验收（2026-11-07）

本轮按“忠实可读显示优先”交付两个 Mac ARM64 版本。当前主流程为选择文档/题型、转换、预览和打开结果包。Windows 和目标系统导入均未验证。

## 发布文件

| 产物 | 路径 | 精确大小 | 固定样本覆盖 |
|---|---|---:|---|
| 轻量版 | `dist/题库转换-macOS-arm64-轻量版.zip` | 42,608,212 字节 / 40.63 MiB | 899/900；化学填空2第62题明确未导出 |
| 可选原图完整版 | `dist/题库转换-macOS-arm64-原图完整版.zip` | 175,194,333 字节 / 167.08 MiB | 900/900 |
| 完整结果包 | `outputs/display-final-complete/batch/最终结果/题库最终结果.zip` | 159,617,665 字节 / 152.22 MiB | 9 个 Excel、9 个题目预览、952 处图片展示、无遗漏 |
| 六题格式对照包 | `outputs/import-acceptance/目标系统导入验收样本.zip` | 16,525,640 字节 / 15.76 MiB | 6 个单题工作簿及对应图片、预览和验收清单 |

独立应用分别位于 `dist/display-light/题库转换.app` 和 `dist/display-complete/题库转换.app`。旧的 `dist/desktop` 和无版本后缀 ZIP 保留为历史产物，不作为本轮交付。ZIP 完整性及 SHA256 见 `outputs/display-release-artifacts.json`。

轻量应用未压缩约 68.9 MiB；完整应用约 475.1 MiB。完整包额外携带约 407.5 MiB 的 LibreOffice 运行时，压缩后增加约 126.4 MiB；不是首次启动下载，也不要求客户安装 Office、MathType、Python、Node、Rust 或字体。两个版本没有同时混装在一个分发包中。

## 公式与原图

- 4709 个已有 LaTeX 公式逐个回放。4459 个沿用严格路径；250 个只因 Unicode 警告回退的公式通过受限字符路径显示。未知宏、私有字符和未覆盖字形仍拒绝显示。原始 LaTeX 字节不改。
- 兼容范围包含裸中文/全角字符、圈号、罗马数字、比例号、prime 等，58 个可见码点均在随包 Noto 字体 cmap 中；U+200B 保留为无墨迹分隔符。字体 SHA 和覆盖表固定。
- 转换状态与显示状态分开。Node 回放构造 4709/4709，通过不等于逐个对照 Word 的视觉验收；未宣称所有公式都人工逐项检查过。
- 原图完整版解决第62题的三个原始 WMF 对象，重放原图元，保留 Δ、↑、点燃、双横线和下标。没有把未知矩阵对齐参数129解释成默认居中，也没有通过 OCR 或模型猜公式。总计4个公式使用原图 PNG（其中1个此前已支持）。
- LibreOffice 仅使用隔离配置中的 Liberation、OpenSymbol 和随包 Noto；不依赖系统安装的中文字体。未知字体、编码和字形仍留在失败路径。源码、绑定、SHA、位置和字体证据留在诊断任务中。
- 可选运行时是本机已有的 LibreOfficeDev 26.8 开发版本；保留其完整 LICENSE/NOTICE。它是此 Mac 样本验证过的选项，不代表 Windows 或未来任意文档能力。

## 已执行的检查

| 检查 | 结果与证据 |
|---|---|
| Python 回归 | 300 项通过，包含3个原始矩阵 WMF 的真实运行；`outputs/display-tests.log` |
| Unicode 显示、负面输入及字体校验 | Node 检查通过；`outputs/display-node-tests.log`、`outputs/display-validation/replay-all-4709-font-covered.json` |
| 最终轻量独立程序 | 受限 PATH 下 899/900；与先前轻量结果的题目、Excel 单元格、图片字节、报告语义一致；`outputs/display-final-light/validation.json` |
| 最终完整独立程序 | 受限 PATH 下 900/900；与开发核心题目、Excel 单元格、图片字节、报告语义一致；`outputs/display-final-complete/validation.json` |
| 原生窗口 | 实际选择9份 Word 并转换900题，查看箭头、图片选项、中文下标和分式；另用轻量版验证99/100部分失败及损坏文件全失败 |
| 小窗口 | 约780×620逻辑像素窗口检查物理解析、分式和题号导航，可阅读并滚动 |
| 离线 HTML | 解压结果后由本机 WebKit 本地文件窗口加载。化学填空2显示521/521处公式，字体加载检查通过；三张矩阵公式原图肉眼核对可读。此检查不是所有浏览器或目标APP认证 |
| 解压后相对路径 | 最终 ZIP 解压到含中文和空格的`outputs/分发 验证/完整版`，PATH仅系统目录，实际运行化学填空2为100/100；`outputs/分发 验证/运行结果/summary.json` |
| 结果包 | ZIP CRC通过，无源DOCX/OLE/XML/任务JSON或macOS资源分叉；9份Excel、9份完整题目预览，共享字体只存1份 |

题目预览从实际 Excel 字符串和行映射生成，图片先验 SHA 再展示。图片预留宽高并取消延迟加载，避免题号跳转后因图片展开而移位。字体/图片无法加载时明确报错，不将只生成 HTML 视为显示通过。

## 界面文案与截图

去掉对话承诺、开发要求复述和大段实现解释。主视图保留数量、成功/不完整/失败状态和必要影响；技术原因、源定位和公式文本放在展开详情中。保留原图标、布局和独立窗口，不新增 Chrome 窗口。

- 空态：`outputs/display-native/empty.png`
- 就绪与处理中：`outputs/display-native/ready.png`、`processing.png`
- 全部完成：`outputs/display-native/completed-900.png`
- 部分失败/全失败：`outputs/display-native/partial.png`、`failure.png`
- 最终未导出说明：`outputs/display-native/problem-final.png`
- 化学条件箭头：`outputs/display-native/chemistry-preview.png`
- 中文下标及分式：`outputs/display-native/physics-cjk-analysis.png`
- 小窗口：`outputs/display-native/small-window.png`
- 三个矩阵原图：`outputs/display-native/offline-q62.png`、`offline-q62-lower.png`

完成状态截图来自相同解析/显示代码的本轮原生窗口运行；后续最终打包仅补齐静态文案、无障碍标签、问题详情折叠和离线图片尺寸。最终轻量包的问题页面另行实际打开并截图。最后一次两个冻结程序的全样本回归均已通过。

## 本轮格式验收与对照样本

本轮按现有 Reference 模板和已约定的导出规则验收，不开展真实系统导入，也不以系统入口作为本轮交付条件。系统必填性、额外编码及图片上传协议仍未验证；现有配置不代表未知系统的正式导入协议。

已完成的格式检查：

- 选择模板的14列、考点模板的4列保持列名和顺序；导出的题目、选择项、答案、解析逐格与解析结果核对，保留源题号到Excel行的映射。
- 选择题类型按配置输出，难度为低/中/高的稳定随机值；未约定的ID、sid和questionType留空。
- 填空题一道大题一行，保留公共题干、小问及答案顺序，考点默认留空；六题对照包包含多小问实例。
- 图片引用对应实际资源，导出图片集合和SHA核对通过；各文档独立目录，避免同名资源串用。公式保留LaTeX标记，原图回退保留在对应位置。
- 完整冻结程序的9份样本均识别100题，总计900题；与开发核心的题目、Excel单元格、图片字节及报告语义一致。结构归属检查与代表性人工核验均有记录，不等同于900题逐项人工语义认证。
- 最终ZIP与诊断/临时数据分开；客户包不包含源DOCX、OLE/XML、任务JSON或日志。轻量版剩余1题明确列入遗漏报告。

证据：`scripts/check_reference.py`中的`check_ownership`、`check_export`，`outputs/display-final-complete/validation.json`，`outputs/display-final-light/validation.json`及`outputs/display-release-artifacts.json`。

最小验收包每目录仅1题，Excel第2行对应所列源题号。由产品同一导出器生成，并逐格与最终冻结程序的全量工作簿交叉核对；不另造题目、答案或编码。

| 样本 | 源文件与题号 | 预期检查 |
|---|---|---|
| 普通选择题 | MathType公式化学2，第5题 | 题干、选项、答案、解析、难度及列映射 |
| 点燃/Δ箭头 | MathType公式化学2，第1题 | A/C点燃在箭头上方，D的Δ在箭头上方，答案B |
| 中文下标 | 微软公式物理，第10题 | 解析中的U总下标、分式、四个电路图选项 |
| 矩阵原图 | MathType公式化学填空2，第62题 | 三张图片对应答案原位置，Δ/↑/点燃/双横线/下标不丢失 |
| 多图题 | MathType公式化学2，第3题 | 四个图片选项各归其位；不同目录同名图片不串用 |
| 多小问填空 | MathType公式化学填空2，第1题 | 一道大题一行，(1)～(4)题干及答案完整，考点空 |

上述六题包保留原文件名“目标系统导入验收样本.zip”，本轮作为格式对照材料使用。没有向任何目标系统上传文件；真实入库、APP公式渲染及图片关联属于未验证的后续边界，不阻塞本轮模板格式交付。

## 仍未验证

Windows原生构建/运行、客户Windows11 x64便携包、断网非管理员/只读安装目录、客户安全策略、签名公证、其他架构与最低系统版本，均未取得实测证据。当前为本机Mac ARM64独立运行、离线显示与导出验收，不标记“系统对接完成”。
