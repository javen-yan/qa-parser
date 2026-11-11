# 第三方组件说明

本项目仅集成小型依赖包和一个独立源码模块，不集成完整第三方题库项目。

| 组件 | 已验证版本 | 许可证 | 分发注意 |
|---|---|---|---|
| lxml | 6.1.3 | BSD-3-Clause；wheel附带原生库说明 | 最终应用分发时一并保留对应wheel及其libxml2/libxslt等原生组件的版权/许可文本。 |
| olefile | 0.47 | BSD-2-Clause | 保留包内许可。 |
| omml2latex | 0.1.1 | Apache-2.0 | 保留包内许可及适用声明。 |
| XlsxWriter | 3.3.1 | BSD-2-Clause | 保留包内许可。 |
| Pillow | 12.3.0 | MIT-CMU（实际包元数据） | 栅格图片解码与PNG输出；后续分发需保留其随附许可。 |
| libwmf / wmf2gd | 本机0.2.13 | 本地配方标注LGPL-2.0-or-later AND GPL-2.0-or-later AND GD | 外部小工具，当前仅开发机使用；其动态库、字体及最终客户再分发条件未验证。 |
| formula-flow的mtef.py模块 | 固定提交9d5737aaa4514f2d12b5206a7357382fdfada2fb | MIT | 许可随 `qa_parser/vendor/mtef.LICENSE` 打包，修改和来源见公式组件文档。 |
| Python运行时/标准库 | 本机3.13.3 | PSF及随附第三方许可 | 当前依赖开发机运行时；未来便携包如携带运行时须保留其完整许可。 |
| cryptography | 46.0.3 | Apache-2.0 / BSD-3-Clause | Windows/macOS 离线更新包的 Ed25519 签名校验；保留 wheel 随附许可。 |
| cffi | 2.0.0 | MIT | cryptography 运行时依赖；保留 wheel 随附许可。 |
| PyObjC | 13.0 | MIT | macOS 原生窗口与独立更新管理器对话框；保留源码许可。 |

没有引入上游的Microsoft Office XSL文件，没有集成GPL的mtef-rs。这里是工程依赖记录，不表示未来便携包的再分发审查或客户环境验收已完成。

本地界面 v0.3.0 使用自行编写的 HTML/CSS/JavaScript 和 Python 标准库，不新增第三方运行依赖，无远程字体或 CDN。Playwright 只用于开发验证，不包含在应用运行依赖中。当前双击启动脚本调用现有 Python 环境；尚未重新分发 Python、浏览器或 libwmf。

## 2026-11-07 离线显示版本

当前独立包实际携带 Python、pywebview、KaTeX、libwmf 和相应依赖；以上早期开发记录不再描述当前打包状态。完整许可证及构建依赖清单随应用的 `Contents/Frameworks/licenses` 分发。

新增 `NotoSansCJKsc-Regular.otf`：SIL Open Font License 1.1。原字体名称未改，完整 OFL 随应用和结果 ZIP 的 `_display` 目录分发；原文件 SHA256 和码点覆盖见 `src/qa_parser/web/vendor/fonts/manifest.json`。

可选完整版额外携带本机 LibreOfficeDev.app（26.8 开发版本），不剥离其 `Contents/Resources/LICENSE`、`NOTICE`、运行时字体或其他资源。它包含多种许可，不将 OpenSymbol 误标为 OFL；再分发依据以随包许可为准。此可选包是当前 Mac 验证产物，Windows 原生运行与客户环境仍未验证。原图回放的字体隔离、候选和限制见 `docs/original-wmf-matrix-probe.md`。
