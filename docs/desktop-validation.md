# Mac 独立窗口与最终包验收（2026-11-07）

此文为初版桌面包的历史验收记录。后续逐题预览升级及当前发布包信息以
[问题预览验收](problem-preview.md) 和 `outputs/problem-preview-ui-validation.json` 为准。

## 交付入口

- `dist/题库转换-macOS-arm64.zip`：独立应用压缩包，28,739,022 bytes（约 27.4 MiB）。
- `dist/desktop/题库转换.app`：已解压应用，可直接双击。
- `启动题库转换.command`：开发入口，仍需要项目 `.venv`，不提供给客户代替独立包。

本轮复用 Python 内核，使用 pywebview 6.2.1 的 Cocoa/WKWebView 窗口与 PyInstaller
6.22.3 封装。没有打开额外 Chrome 标签，没有引入 Electron/Chromium、在线字体或 CDN。
运行资源随包携带；开发工具留在开发端。API 与关闭事件依据
[pywebview 文档](https://pywebview.flowrl.com/api/)核对，原始依赖版本记录在
`requirements-desktop.lock`，它是本机 Mac 构建环境锁定，不是 Windows 安装清单。

## 实际完成的检查

| 检查 | 结果与证据 |
|---|---|
| macOS 正常应用启动 | `/usr/bin/open` LaunchServices 启动 `.app`，原生窗口显示现有页面，无浏览器地址栏 |
| 重复启动、关闭 | 原生关闭按钮、界面退出均结束窗口；关闭后再次启动成功 |
| 子进程 | 真实进程组测试清理服务及忽略 SIGTERM 的渲染子进程，未终止无关进程；最终进程扫描无本次桌面服务残留，原用户浏览器服务保留 |
| 默认与最小窗口 | 默认 1120×800、最小 760×600；初始尺寸及最小尺寸受当前屏幕可用区域限制 |
| 布局 | 实际拖拽缩小再放大，验证空态、九文档列表、完成状态、问题筛选、文件夹及报告弹窗；列表独立滚动，底部操作持续可达，无横向滚动或主要控件裁切 |
| 原生上传、实际转换 | 窗口中选取全部九份样本，每份 100 题；831 导出、69 未导出，界面明确“结果不完整” |
| 最终结果入口 | 点击“取得最终结果包”，Finder 目录仅包含最终 ZIP 和批次 TXT |
| 打包语义 | 搬到中文空格路径、PATH 仅 `/usr/bin:/bin`，九份样本 Excel 单元格、图片名及 SHA、遗漏题号原因与基线一致 |
| 解压后的交付包 | 混合题型、同名重复、损坏文档：287 导出、13 遗漏、1 文件失败；3 Excel，724 处图片引用可定位 |
| 公式依赖 | 98 个 Mach-O 无绝对 Homebrew 依赖；326 个受支持 WMF 转换与原工具字节相同；26 个字体文件与携带源码一致 |
| 只读程序目录 | 从独立复制并移除写权限的 .app 执行真实公式图片样本，93/100 与基线一致，工作数据写到独立目录 |
| 禁网 | 用 macOS 进程沙盒禁止全部网络，真实化学公式图片文档仍得到 93/100，7 题明确未导出；没有修改系统网络设置 |
| 自动测试 | 152 passed，最后一轮 3.41 秒 |

最终包采用文件白名单，只有每份文档的 `questions.xlsx`、`images/`、中文结果说明；
不带任务 JSON、源 Word、原始公式、缓存或日志。所有遗漏保留题号与原因。
全失败批次只生成失败说明，不生成空成功 ZIP。源文件同名时按上传顺序建立编号子目录。

工作文件位于 `~/Library/Application Support/QA Parser/desktop/<session>/jobs/`，
最终包位于用户选定保存目录的新批次子目录。目录均不覆盖旧结果。
关闭处理中的窗口会停止当前处理及其渲染进程；未完成的工作文件保留在诊断目录，
不应被当作最终包。当前没有重开窗口后自动续跑中断批次的功能。历史会话的服务记录
可能保留供诊断，新启动使用独立会话，不能根据旧 server.json 判定服务仍在运行。

## 证据

- `outputs/desktop-validation/validation.json`
- `outputs/desktop-validation/offline-formulas/summary.json`
- `outputs/desktop-validation/readonly-program-check.json`
- `outputs/bundled-all-nine/frozen-semantic-check.json`
- `outputs/bundled-archive-smoke/archive-smoke-check.json`
- `outputs/delivery-package-validation/validation.json`
- `output/desktop/default-empty.png`、`minimum-empty.png`、`minimum-batch.png`
- `output/desktop/minimum-completed.png`、`minimum-folder.png`、`minimum-report.png`、`default-completed.png`

## 仍未验收

仅本机 ARM64 macOS 已验证。Windows 原生构建/执行、固定 WebView2、Intel Mac、最低系统
版本、客户无开发环境的全新机器、Developer ID 签名与公证、组织安全策略、真实 APP 的
导入协议及公式/图片显示仍未验收。当前 ad-hoc 签名只用于本地构建校验，不能承诺任意
客户机器解压即开。第三方许可与对应源材料已随包保存，外部分发前仍需按实际分发方式
核实 GPL/LGPL 等义务；详情见 `desktop-packaging.md`。

这次完成的是独立 Mac 调试/使用闭环与可搬移产物，不把 Windows 草案或 Mac 结果视为
Windows 客户验收，也不把结构转换通过视为全部公式语义人工验收。

## 产品图标

`assets/icon.svg` 为可编辑矢量源，`scripts/make_icon.py` 同步生成 PNG、ICNS 和 ICO，
无字体、在线图片或新增渲染依赖。图案为蓝底文档与转换箭头。正式 Mac bundle 使用
`icon.icns`，应用名称、显示名称、可执行文件均为“题库转换”。Finder 已实际检查，截图
`output/desktop/finder-icon.png`；应用包和 ZIP 中的 ICNS 与源资产一致，见
`outputs/native-icon-validation.json`。Windows ICO 只准备资产，未实机验证。

开发 `.command` 通过 Python 解释器启动，系统可能仍将开发进程标识为 Python；客户使用
正式 `.app`，无需该脚本。

Dock 专用窗口截图已核对：`output/desktop/dock-icon.png`，运行中的题库转换显示新版
蓝色文档箭头图标。全屏截图因隐私范围被自动审批拒绝，改用仅 Dock 自有窗口捕获完成；
未采集全屏。最终应用保持空态打开供使用。
