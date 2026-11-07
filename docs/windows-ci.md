# Windows 云端构建与验收范围

## 入口

`.github/workflows/windows-portable.yml` 仅手动触发（workflow_dispatch），权限为 contents:read，不推送代码、不发布Release。使用Windows Server 2022 x64 runner和Python 3.13.3，输出14天保留的候选ZIP及独立检查证据。GitHub Actions runner不是客户Windows 11电脑。

本地/CI构建入口：`pwsh packaging/windows-build.ps1`。运行依赖只在构建时获取，固定来源及SHA见 `packaging/windows-runtime-lock.json`。客户端解压整个文件夹，双击“题库转换.exe”，不需安装Python、Node、Office或MathType。运行时和字体随包携带；缓存和任务写入LOCALAPPDATA下的QA Parser，不写程序目录。

## 与Mac保持一致的部分

使用同一Python解析/导出核心、原有产品UI、KaTeX适配及Noto离线字体。Windows只增加固定浏览器目录定位、系统打开结果操作、子进程无控制台选项及冻结程序检查入口。默认运行仍打开独立原生窗口，不启动Chrome。

WebView2必须由包内`_internal/webview2`提供；缺少组件时报错，不悄悄使用机器上的Edge或IE。pywebview 6.2.1采用`WEBVIEW2_RUNTIME_PATH`设置，强制edgechromium，并为浏览器缓存指定可写用户目录。Python/.NET桥依赖随包；Windows自带的.NET Framework和系统运行库仍需在支持矩阵内实测。

## 检查顺序

1. `scripts/check_public_tests.py`执行公开合成回归，列出排除的真实样本/POSIX专用测试；意外skip会失败。该报告不是9份真实题库验收。
2. Node运行同一数学显示适配测试。
3. 原生冻结Windows EXE，收集固定WebView2、WMF原生DLL、字体和许可证。记录资源哈希清单。
4. `scripts/windows_smoke.py`生成全新的合成DOCX：OMML分式、图片选项、分析/详解、多小问填空、损坏文件及混合批次。比较开发核心和冻结程序的Excel单元格、难度、图片字节与结果ZIP，不以退出码为唯一依据。
5. 受限PATH与中文空格路径运行冻结程序。合成WMF绘制线和ASCII文字，实际调用包内原生转换器。原生窗口检查产品DOM、字体加载及数学适配，记录独立报告。
6. 只有全部门槛通过才输出候选ZIP；失败只上传诊断证据，不将不完整包标为成功。

真实`reference/`及`tests/fixtures/original-wmf/`不提交。源样本仍在Mac，不自动上传、不下载私人附件、不以缺样本跳过来声称900/900。未来获授权后另设真实样本验收，不改变本轮合成测试的证据范围。

## 原生依赖与限制

- WebView2 Fixed Version：微软官方固定版CAB，版本/实际SHA固定到锁文件；保持运行时许可文件。更新必须重新固定校验值、构建及回归，客户运行时不联网更新。微软说明固定版不可从UNC/网络路径运行，Windows 10可能需要额外ACL，不将其纳入首个客户目标。
- WMF：MSYS2 UCRT x64的libwmf，使用Windows原生EXE及递归PE依赖；原生库/字体许可证和相应源码记录随包保留。与Mac Homebrew构建不是同一二进制，不直接复用Mac dylib。保留原有字体和WMF审计门槛，无法可靠处理的内容仍明确隔离。
- 可选LibreOffice：Windows官方MSI是可调查来源，但Mac fontconfig隔离方式不等同于Windows字体系统。本轮不把Windows LibreOffice启用为可靠原图引擎，也不承诺Mac完整版900题覆盖；需要固定Windows版本、校验、许可、独立字体替代和原图语义实测后另行开启。
- 非管理员、断网、只读程序目录、客户Windows11、安全软件拦截与签名、最小OS版本：最终以对应环境实测为准。CI构建和合成GUI检查不能代替客户验收。

## 官方依据

- [pywebview 6.2.1 API与WEBVIEW2_RUNTIME_PATH](https://github.com/r0x0r/pywebview/blob/6.2.1/docs/api/README.md)
- [pywebview冻结打包](https://github.com/r0x0r/pywebview/blob/6.2.1/docs/guide/freezing.md)
- [微软WebView2固定运行时分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [微软运行时下载](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
- [MSYS2 UCRT libwmf版本、SHA及源码](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-libwmf)
- [GitHub手动工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub官方artifact action](https://github.com/actions/upload-artifact)
- [LibreOffice官方Windows分发](https://www.libreoffice.org/download/download-libreoffice/)
