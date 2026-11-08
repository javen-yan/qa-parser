# 客户公式交接与图片兼容实验（2026-11-08）

## 已知和未知

截图中的两道电路题与本地 `微软公式物理.docx`、`MathType公式化学3.docx` 第 57、92 题内容对应。客户实际上传的 Word、Excel、导入处理及 APP 渲染链路不可获得；不等待这些输入继续完成本地工作，也不据此断言客户端有错。截图中的 `<em>`、下划线缺失与 LaTeX 裸露符合某些富文本后处理污染的现象，但同样需要考虑分隔符切分、转义/反转义、版本差异、局部渲染失败等路径。

## 可复现分层记录

`scripts/audit_formula_handoff.py --task TASK_DIR --export EXPORT_DIR --out NEW_DIR --probe-images` 只读源证据和任务，不改写公式。默认核对源第 57、92 题，输出：

- 公式源对象的 SHA、位置、OMML/MTEF 格式及预览绑定；源内容仍在任务 evidence 中。
- 任务解析字段与实际 XLSX 解析单元格的逐字比较。
- 原始 LaTeX、`$$...$$` 边界、一个故意不保护公式的下划线强调替换，以及错误的单美元切分对照。
- 可选原始预览 PNG 转换探测，保留引擎、失败原因和源图哈希。

强调替换只是最小机制示例，**不是 Markdown 库、不是客户程序，也不能证明标准 Markdown 会产生截图中的相同结果**。例如 `I_1R_x=I_2R_0` 经简单下划线配对会出现 `I<em>1R</em>x=I<em>2R</em>0`，说明为何不能先任意处理强调语法再渲染公式。另有 `$$` 与 `$` 切分不一致会留下空片段或裸 LaTeX 的对照。不能以全局删 `<em>`、全局加反斜杠或改默认分隔符作为未经验证的修复。

本地机器记录：`outputs/customer-numbered-validation/{omml,mathtype}-handoff/`。两个版本各 67 个公式对象通过随包 KaTeX 严格、不改源码的显示构建；其中解析字段 51 段。OMML 版本实际 Excel 与任务内容相同；这不是客户实际上传文件的证明。

## 原图与重绘是两条不同路径

### 已有原图兜底

本轮选中两题 16 个含电阻等式或分式：OMML 没有可靠绑定的原始预览；MathType 的 16 个 WMF 均被现有审计因 Symbol 字体拒绝。因此现有轻量原图路径不能可靠覆盖这些公式。不能绕过字体检查，仅凭工具退出码 0 当作保真。

### 已验证 LaTeX 离线重绘 PNG：本地可行性通过

在一个复用的本地 Chromium 会话中，使用本仓库随包 KaTeX、字体及显示模块生成 25 种不同公式的 PNG，再加 1 张合成中文控制。等待字体完成加载后截取公式元素；全部 26 张均通过可见墨迹边界留白检查，并人工查看对照图：分数线、上下标、括号、电压/电流变量及中文控制可辨识，无可见截断。首次实验发现邻近源码标签侵入截图边缘，已增加独立间距并重拍；这说明仅“渲染成功”不足以保证可用图片。

产物在 `output/playwright/formula-compat/`：

- `comparison.html`、`input.json`、`f*.png`、`unicode-control.png`、`raster-report.json`、`contact.png`：来源与重绘对照。
- `build_demo.py`：仅本地验证夹具；将两题的公式替换成现有 `参考0001.png` 语法，并保留原始题图。
- `image-demo/最终结果/题库最终结果.zip`：两题试验 Excel + 同名图片目录，共 67 个公式图片引用和 6 个原始题图引用；`bindings.json` 保留每个引用对应的公式或题图来源。

这不是 Word 原始渲染保真认证，不更正源公式，不自动用于其他公式。图像仍依赖客户系统已有图片上传/关联能力，行内基线、缩放、长公式换行、清晰度和 APP 展示没有验收。截图样例包含错误选项也是原题的一部分，不能当成转换错误自动改掉。

## 早期 Chromium 实验的边界（历史记录）

早期重绘 PNG 使用开发端 Playwright/Chromium 完成实验，当时未加入产品运行依赖，也没有切换默认导出。已有产品含 KaTeX/字体/桌面 WebView，**不等于已实现跨平台 PNG 捕获接口**。后续可研究 Windows WebView2 与 Mac WKWebView 的原生捕获桥接，或已验证的离屏绘制路径；必须分别验证字体加载、像素比例、透明背景、尺寸上限、超时、中文路径及便携运行。CLI 没有现成桌面窗口，不能直接宣称同一 PNG 路径可无界面运行。若另行捆绑浏览器，需要评估体积、许可证和部署，当前没有增加这类依赖。

该实验阶段只证明了本地样例的可行性。下节记录后续实际产品实现，替代这一阶段的状态；v0.3.0 正式包仍未替换。


## 混合输出已接入（后续实施，覆盖上文实验阶段状态）

当前默认采用 `mixed-formula/1`，不再只是手工样例：简单表达式和原子分式（如 `U=IR`、`R=\frac{U}{I}`）保留文本；多下标、复合下标表达式、上下标叠加、嵌套分式和不在保守文字子集中的结构要求图片。单独的 `R_x` 可以保留，但同一导出单元格内多个带下标公式（含中文间隔、含合并后的“选择项”列）都会转入图片路径。规则是可解释的风险启发式，不是客户 APP 语法认证，也不自动改写 LaTeX。

需要图片时先使用有绑定证据且通过字体/内容检查的原始预览；原图不可用时，只允许对结构适配已接受的 LaTeX 重绘。转换未知、标签污染、绘制失败或裁边检查失败时保留证据并隔离对应题目。每张重绘图保存源对象 SHA、LaTeX SHA、PNG SHA、渲染器/字体资源指纹和平台信息；校验后才进入统一编号和 Excel 导出。原始题图与公式图片按文档出现次序统一编号，不覆盖内部原图文件。

已实现 Mac 隐藏 WKWebView 原生截图，以及 Windows 固定 WebView2 的 CapturePreviewAsync 适配；Windows 捕获、裁边及线程调用已通过下述原生 CI 实际运行，未因此宣称客户环境可用。两者共享同一 KaTeX/字体、策略、业务与验证逻辑，不增加 Chromium/Node 客户端依赖。Windows 页面通过仅服务一份内嵌离线资源页面的 loopback 服务加载，避免 NavigateToString 大小限制；捕获发起在 UI 线程，等待和批处理在工作线程，外层 helper 有超时和进程树清理。

Mac 开发环境已完成 100 题混合转换；新脚本 `scripts/mixed_formula_smoke.py` 对开发态及冻结程序覆盖：原子分式保留、多个下标与跨公式中文间隔、分式/根式/矩阵/箭头/中文原生绘图、未知源矩阵拒绝猜测、坏公式报告、重复名拒绝、中文空格路径、图片引用及统一 ZIP。CI 只用合成输入，不上传 Reference；现有 `windows-portable.yml` / `macos-portable.yml` 已加入相同混合验收门槛；使用验证分支运行，无 Release 写权限，不会覆盖稳定版。

原生 API 文档：[Apple WKSnapshotConfiguration](https://developer.apple.com/documentation/webkit/wksnapshotconfiguration)、[Microsoft CapturePreviewAsync](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.capturepreviewasync)。这两种 API 分别返回原生页面快照与 WebView 捕获流，不能单凭 API 调用成功宣称公式保真；还必须经过像素、字体、语义与人工视觉核验。

## 本地完整样本复测

`d270cf3` 完成本机原生解析回归，9 份 Reference 各识别出 100 题，导出 899 题；仅《MathType公式化学填空2》第 62 题的三个 MTEF 多行对象结构未支持，且原始 WMF 字体审计未通过，整题进入复核。未放宽结构或字体检查。结果位于 `outputs/mixed-formula-validation/nine-final/`，该目录的 `regression-summary.json` 和最终包记录每份文件及遗漏题号，不上传私有样本。

首轮为 896/900：另外三题触发 Retina 重复放大后的像素上限。修复 WKSnapshotWidth 点数与 backing scale 的换算后，三题均恢复成功，其中长公式输出为 2612×170 像素，保留完整墨迹边界。不是增大尺寸上限或放过裁切图。

本机公共回归 318 项通过、79 项按现有清单排除、0 项意外跳过。新增生命周期测试验证窗口关闭回调与 finalizer 并发时只清理一次自身进程组；原有测试继续验证 renderer 子进程清理和无关进程存活。Windows 冻结测试中移除 Python UTF-8 环境后暴露了系统代码页问题，后续 `1b16db7` 将原生 helper 输入、输出、报告及验收读取统一为显式 UTF-8。


## 三平台原生验收（2026-11-08）

最终运行代码为 `1b16db77323a03df6d9be111e11f42775a806e19`，分支 `codex/mixed-formula-export`。两个工作流最终结论均为 success：

| 环境 | 实际验证 | 证据 |
|---|---|---|
| Windows Server 2022 x64 / 10.0.20348 | 原生构建、解压后冻结 CLI、固定 WebView2 窗口、开发态与冻结态公式捕获及混合导出 | [运行 42477780547](https://github.com/javen-yan/qa-parser/actions/runs/42477780547) |
| macOS 15.8.2 ARM64 | 原生构建、迁移后 `.app`、Cocoa 窗口、开发态与冻结态混合导出 | [任务 128723513098](https://github.com/javen-yan/qa-parser/actions/runs/42477784591/job/128723513098) |
| macOS 15.7.9 Intel x86_64 | 同上，实际 Intel 原生运行 | [任务 128723513083](https://github.com/javen-yan/qa-parser/actions/runs/42477784591/job/128723513083) |

三平台各执行同一合成集：两个不同名称的 Word 共导出 6 题、明确隔离 1 题未知源矩阵；已知 LaTeX 的 6 个原生绘图样例通过，1 个非法上标表达式拒绝。矩阵绘图可用不等于任意 Word 矩阵解析已支持。冻结测试移除开发工具路径及 Python 配置，不依赖客户端另外安装 Node、Python 或浏览器。Windows 复用随包固定 WebView2，Mac 复用系统 WKWebView。

逐项比较三平台的开发态/冻结态 Excel 单元格、处理策略、图片引用编号、难度和遗漏清单，完全一致；规范化 JSON SHA-256 为 `ec1b25cbb5f847b0237a0a4396cfeedd5d97763776621649b5555bb0cded130c`。字体与渲染资源指纹同为 `d77226ae6e2e57b6a8bd344225f78c17218bf2915048a1d78e6d6e7f4a27fa4d`。PNG 字节和抗锯齿随原生引擎不同，不要求像素相同。对照检查了分式/下标、矩阵、中文反应条件箭头、中文电压四类图片，未见缺字或裁边；自动墨迹边界检查覆盖全部六类成功绘图。

本地云端证据摘录在 `outputs/mixed-formula-validation/{windows,arm,intel}-final-evidence.log`；`cloud-visuals/final-comparison.json` 和 `final-comparison.png` 保存对比结果。CI 日志也保留 `MIXED_REPORT` 和 `MIXED_VISUAL`，未上传 Reference。此次关闭 Actions 产物上传，只执行构建/运行和记录合成证据，没有新增 Release、公开 OSS 包或覆盖 v0.3.0。

这证明所列 CI 环境下冻结程序的对应行为，不替代 Windows 11 客户机的断网、非管理员、安全策略、签名和实际 APP 导入验收。混合策略的文字子集仍是保守规则，原始预览不可用时的图片是已接受 LaTeX 的重绘，不声称复刻 Word 原始排版。
