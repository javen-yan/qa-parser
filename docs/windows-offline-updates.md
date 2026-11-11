# v1 Windows 双版本分发

产品从 v1.0.0 起使用双版本 Windows 包。Mac ARM64/Intel 继续使用完整 `.app` 分发；下一版本起附带独立的 `管理更新.app`，支持离线签名更新和回退，见 [macOS 离线签名更新](macos-offline-updates.md)。字体、Python、业务依赖、导出布局和解析规则均未迁移。

## 客户使用

首次完整解压 Windows ZIP 到当前用户可写的目录，双击根目录“题库转换.exe”。不要只复制 EXE。旧版用户手动安装一次新完整包；原有 LOCALAPPDATA/QA Parser 数据继续保留。

日常更新：关闭所有程序窗口并等待转换结束，运行“管理更新.cmd”，选择安装离线更新，导入 `.qaupdate`；也可以直接在程序窗口点击“离线更新”，程序退出后由常驻管理器完成安装并重新打开。可以跨越多个业务版本，但运行时必须匹配。运行时不兼容时安装新的完整包。管理入口也能恢复上一版本。不自动联网，不提权，不修改源 Word 和已生成结果。

安装目录包含独立管理入口及其依赖、`runtime/r1-<SHA256>/`、`app/v1.x.x/`、`install-state.json`。运行时 ID 基于全部运行时文件清单，不依赖产品版本。字体继续随业务包，避免改变 Mac 与结果预览路径。

## 构建与签名

Windows 原生流程先构建并验证旧布局的业务程序，再执行 `scripts/build_windows_distribution.py` 生成拆分包。固定版 WebView2、WMF、原生许可证和源码归档移入运行时；Python 的许可证保留在业务包。管理入口独立 PyInstaller 构建，使用 Win32 对话框，无 WebView 或业务 Python 依赖。

签名使用 Ed25519；仓库 `packaging/update-public-key.txt` 固定公钥，并与 `QA_UPDATE_PUBLIC_KEY_HEX` 仓库变量核对。`QA_UPDATE_PRIVATE_KEY_HEX` 是仓库 Secret，内容为 32 字节 seed 的十六进制。密钥缺失或不匹配时构建失败。私钥不放入源码、构建参数、更新包或日志。更换签名公钥需要新的完整包，不支持静默密钥轮换。

本次密钥备份位于维护者本机 `~/.codex/keys/qa-parser/windows-update-ed25519.seed`，权限 0600；该路径不属于仓库。应将备份纳入维护者的安全备份流程。

业务包包含规范化 JSON 清单及原始 64 字节签名，清单绑定平台、版本、管理入口协议、运行时 ID、每个文件长度和 SHA-256。拒绝未列出文件、重复或大小写冲突路径、Windows 特殊路径、符号链接和加密 ZIP。

## 更新事务

程序内“离线更新”会写入一次性请求并退出；常驻管理器在业务进程退出后读取该请求，完成安装并重新启动。请求文件只接受 `.qaupdate`，处理前即删除，避免重复安装。

管理入口独占 operation 锁。运行期间，管理入口和业务入口持有共享 activity 锁；更新/恢复阶段需要独占 activity 锁。业务进程启动后先等待管理入口将其加入 Windows Job Object，再启动服务和渲染子进程；管理入口意外结束时，所属进程树退出，避免留下无锁的转换进程。

业务包解压至 `.update-staging`，校验后改名为独立版本目录，再原子更新状态文件。状态包含 current、previous、pending。新版本收到页面就绪确认后清除 pending；启动失败或下次发现未完成的 pending 时恢复旧指针。转换错误不会回退。保留旧版本，不自动清理历史运行时。测试更新的合成版本只写入 build/，不得发布。

## 验证与发布

`native-validation.yml` 在隔离验证分支或手动调度时运行三个原生平台，不创建或发布 Release。Windows 额外执行实际签名升级、坏 EXE 启动回退、运行时摘要不变的检查。正常发布流程将更新包、运行时 ZIP、体积报告作为 Windows 附属产物，保持两个 Mac 产物不变。

本地单元测试不能替代 Windows 系统 API、打包程序或 Mac 原生验收。私有样本只在本机跑。稳定发布政策在原生证据齐全前保持关闭；客户实机、Developer ID 公证和目标系统导入仍需单独验收。
