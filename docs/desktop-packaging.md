# 独立桌面打包

桌面壳使用 pywebview 调用 macOS Cocoa / WKWebView，显示项目现有离线页面。
Python 内核、依赖、静态 UI、WMF 转换器、字体和许可证随 `.app` 携带；开发环境
`.command` 不是该分发包。核心默认配置是 Python 内置常量，不依赖项目 config 路径。

## Mac 构建

开发端安装 `pywebview`、`pyinstaller` 及 pyproject 的运行依赖，使用本机 libwmf。
源码归档说明见 `packaging/vendor-sources/README.md`。

```sh
.venv/bin/python scripts/build_desktop.py
```

产物 `dist/desktop/题库转换.app`。构建缓存仅在项目 `build/desktop`。
打包脚本递归收集 wmf2gd 的非系统动态库，以 `@loader_path` 重写加载路径并检查，
保留系统 `/usr/lib` 和 `/System/Library` 依赖，不使用客户 Homebrew。
字体 XML 原有绝对路径先改为文件名，运行时在临时目录生成真实绝对路径；只读程序目录
无需修改。冻结应用缺少自带 WMF 资源会明确返回失败，不悄悄借用开发机 PATH。

`Contents/Frameworks/licenses` 保留第三方许可证、Python 包版本、Homebrew 配方与收据、
libwmf 原始源码和补丁、FreeType 原始源码以及字体许可。
`build-manifest.json` 记录平台、解释器、原始 native SHA 和未验证项。

目前使用本地 ad-hoc 签名，**不是 Developer ID 签名或公证**。重新分发还需核实
GPL/LGPL 对应源、替换权与完整版权声明；不能将 PyInstaller 的例外理解为替其他依赖授权。
[PyInstaller 许可](https://www.pyinstaller.org/en/stable/license.html)明确要求遵守所用依赖许可。

## 本轮已验证的封装证据

- `--stage-only` 可构建重定位的本地 WMF 依赖和字体资源。
- 对九份 DOCX 中全部 326 个通过原有字体审查的 WMF 预览，使用重定位资源转换的 PNG
  与 Homebrew 原始渲染 PNG 字节完全相同；不代表其他 WMF 都可转换。
- 冻结运行时不借用开发机工具、中文和空格字体路径的单元测试已加入。
- 初版 `.app` 实际复制到 `/tmp/题库 独立包验证`，以 `PATH=/usr/bin:/bin` 启动
  `--cli run`，化学3样本成功结束，100 题中导出 93、隔离 7，与既有基线一致。
- 后续整包 UI、退出、重复启动和全样本检查由主验收记录补充；上述证据不替代这些项目。

## Windows 状态

`packaging/windows-build.ps1` 是未实测的原生构建草案，要求显式提供匹配架构的 WMF
程序、依赖 DLL、字体和 XML fontmap，不会下载运行环境。它尚未接入固定 WebView2 的
离线定位，**不能作为可交付的 Windows 便携包**。需要授权的 Windows 11 x64 环境后，
再验证 pywebview Edge Chromium、固定 WebView2、WMF/DLL、路径、子进程清理与安全策略。

本机 ARM64 Mac 产物不能证明 Intel Mac、Windows 或更低 macOS 版本可用。
[PyInstaller 构建说明](https://pyinstaller.org/en/stable/usage.html)要求针对目标平台分别构建。

全样本冻结程序验证已完成：`outputs/bundled-all-nine/frozen-semantic-check.json`。
九份 Word 各识别 100 题，输出 831 / 隔离 69，Excel 单元格、图片文件名和 SHA、
遗漏源题号及原因均与开发基线完全一致。整包 98 个 Mach-O 文件检查未发现非系统
绝对动态库依赖。该检查在中文空格路径、PATH 仅 `/usr/bin:/bin` 下运行。
