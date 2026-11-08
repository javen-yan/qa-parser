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

## 跨平台交付限制

目前重绘 PNG 使用开发端 Playwright/Chromium 完成实验，未加入产品运行依赖，也没有切换默认导出。已有产品含 KaTeX/字体/桌面 WebView，**不等于已实现跨平台 PNG 捕获接口**。后续可研究 Windows WebView2 与 Mac WKWebView 的原生捕获桥接，或已验证的离屏绘制路径；必须分别验证字体加载、像素比例、透明背景、尺寸上限、超时、中文路径及便携运行。CLI 没有现成桌面窗口，不能直接宣称同一 PNG 路径可无界面运行。若另行捆绑浏览器，需要评估体积、许可证和部署，当前没有增加这类依赖。

结论：统一文件布局已实现；公式图片兼容有可审阅的两题本地样例，但尚不是产品开关或正式发布功能。默认仍保留原公式 LaTeX 和既有可靠原图兜底，不全量图片化；当前 v0.3.0 正式包未替换。


## 混合输出已接入（后续实施，覆盖上文实验阶段状态）

当前默认采用 `mixed-formula/1`，不再只是手工样例：简单表达式和原子分式（如 `U=IR`、`R=\frac{U}{I}`）保留文本；多下标、复合下标表达式、上下标叠加、嵌套分式和不在保守文字子集中的结构要求图片。单独的 `R_x` 可以保留，但同一导出单元格内多个带下标公式（含中文间隔、含合并后的“选择项”列）都会转入图片路径。规则是可解释的风险启发式，不是客户 APP 语法认证，也不自动改写 LaTeX。

需要图片时先使用有绑定证据且通过字体/内容检查的原始预览；原图不可用时，只允许对结构适配已接受的 LaTeX 重绘。转换未知、标签污染、绘制失败或裁边检查失败时保留证据并隔离对应题目。每张重绘图保存源对象 SHA、LaTeX SHA、PNG SHA、渲染器/字体资源指纹和平台信息；校验后才进入统一编号和 Excel 导出。原始题图与公式图片按文档出现次序统一编号，不覆盖内部原图文件。

已实现 Mac 隐藏 WKWebView 原生截图，以及 Windows 固定 WebView2 的 CapturePreviewAsync 适配；Windows 捕获、裁边及线程调用目前进入原生 CI 验证阶段，未因此宣称客户环境可用。两者共享同一 KaTeX/字体、策略、业务与验证逻辑，不增加 Chromium/Node 客户端依赖。Windows 页面通过仅服务一份内嵌离线资源页面的 loopback 服务加载，避免 NavigateToString 大小限制；捕获发起在 UI 线程，等待和批处理在工作线程，外层 helper 有超时和进程树清理。

Mac 开发环境已完成 100 题混合转换；新脚本 `scripts/mixed_formula_smoke.py` 对开发态及冻结程序覆盖：原子分式保留、多个下标与跨公式中文间隔、分式/根式/矩阵/箭头/中文原生绘图、未知源矩阵拒绝猜测、坏公式报告、重复名拒绝、中文空格路径、图片引用及统一 ZIP。CI 只用合成输入，不上传 Reference；现有 `windows-portable.yml` / `macos-portable.yml` 已加入相同混合验收门槛；使用验证分支运行，无 Release 写权限，不会覆盖稳定版。

原生 API 文档：[Apple WKSnapshotConfiguration](https://developer.apple.com/documentation/webkit/wksnapshotconfiguration)、[Microsoft CapturePreviewAsync](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.capturepreviewasync)。这两种 API 分别返回原生页面快照与 WebView 捕获流，不能单凭 API 调用成功宣称公式保真；还必须经过像素、字体、语义与人工视觉核验。
