# Draft Release 直传验证记录

本轮不等待 Actions artifact 配额恢复，不运行额度探测。`release.yml`使用独占草稿传递候选 ZIP 和元数据；两个构建组件均传入`upload-artifacts: 'false'`。所有上传步骤发生在同一原生 runner 的冻结程序检查之后，最终任务再从 Release 下载三组文件核对 SHA-256。手动运行始终保持 draft，未创建正式版本号。

## 本轮修复

- Mac 原生编译显式设置 SDKROOT，覆盖 FreeType 不继承 CPPFLAGS 的辅助程序。
- Mac 字体、许可证和 JSON 元数据移到 Resources；运行路径通过相对符号链接保留。避免代码签名依赖 ZIP 不保留的扩展属性。真实本地应用副本已完成重新签名、无资源叉 ZIP、解压和严格签名检查。
- Windows libwmf 使用临时字体副本及相对参数，支持中文和空格安装路径。固定的 libwmf 0.2.16 源码以文本模式写 PNG；适配仅逆转有特征签名的 CRLF 编码，并检查字节往返和 PNG CRC，不能通过校验的输出仍失败。
- 原生窗口检查使用 pinned Cocoa/EdgeChromium 的 run_js JSON 返回，不放宽产品 CSP，不使用 eval。
- 默认图片引用精确改为`参考0001.png`，实际资源仍为`images/0001.png`。导出清单记录引用模板，旧任务及显式自定义模板兼容。

## 当前证据

源码提交：`28b131d13b31691d5a02c4f2b1cfed16ee75f561`。

- [原生工作流 42446082512](https://github.com/javen-yan/qa-parser/actions/runs/42446082512)
- [本次独占私有草稿](https://github.com/javen-yan/qa-parser/releases/tag/untagged-4cda3e9c59464b05e729)
- Windows job `128625249647`：248 项公开回归通过，冻结 CLI 与开发核心语义比较、ZIP 中文空格目录解压、内置 WMF、真实 WebView2 窗口、程序目录内容未变检查均通过；ZIP 与元数据直接上传成功。
- Windows ZIP：`qa-parser-windows-x64-candidate.zip`，491,268,444 字节；SHA-256：`5a85af5761a7f2e1a201c5dc3259821329c215453dc05bc5c4f7a00697c61f7b`。
- Mac ARM64 首次运行在本地服务启动处超时，保留失败记录；最终状态待本轮后续验证。不得将本机成功替代云端原生结果。
- Mac Intel 与完整草稿汇总状态待后续验证。

本机公开回归 248 项通过、79 项明确排除、无意外 skip。真实`MathType公式化学2.docx`在本机以新格式导出 100 题，127 张图片均有相对资源，预览无缺图；证据在`outputs/image-reference-contract-real/`。真实 Word 未上传。

Windows 云端环境为 Windows Server 2022 x64；Mac 云端目标为 macOS 15 ARM64/Intel。以上不能替代客户 Windows 11、断网非管理员、签名/公证、安全软件和目标系统导入验收。旧本地分发文件没有被本次构建覆盖。

## 后续诊断（8015259）

Mac 原生运行 `42447429693` 的 Python 栈和原生采样共同确认：冻结服务停在 `HTTPServer.server_bind → socket.getfqdn → gethostbyaddr`，等待 mDNS 反向查询。现改为 TCPServer 的 numeric loopback bind，明确设置 server_name/server_port，不进行反向 DNS；没有扩大监听范围。新增 DNS 不可用回归，完整公开回归 254 通过、79 明确排除。原生工作流 `42447683505` 正在验证此修复，结果仍以实际运行记录为准。
