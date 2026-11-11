# Word 题库转换工具

离线读取 DOCX 中的选择题或填空题，生成 Excel、图片及可离线查看的题目预览。采用同一 Python 解析核心、CLI 和独立桌面窗口，客户无需安装 Python、Office、MathType、Node 或开发工具。

客户最终包统一按原 Word 文件名组织：`1.docx → 1.xlsx + 1/`，`物理练习.docx → 物理练习.xlsx + 物理练习/`。同名目录只放图片；说明与离线预览单独位于 `_说明/`。保留前导零，重名或跨平台路径冲突明确报错。桌面单份/批量、CLI `batch` 与 `run` 使用相同交付布局。内部诊断目录不是最终包。详见 [导出约定](docs/export-contract.md)。v0.3.1 包含此布局及混合公式输出更新；v0.3.0 不包含。

v0.3.3 增加共享材料复合题拆分、逐题分类、填空答案绑定与完整考点回填、表格角色判断和局部失败报告。新样本含重复正文为748/863导出、96策略跳过、19复核；原九份按新策略896/900导出。纯题型模板保持，混合文档在同名Excel中分“选择”“考点”工作表，目标系统多工作表导入与APP显示未验收。详见[导出约定](docs/export-contract.md)。

v0.3.2 优先将可靠公式输出为 `$$...$$` 文本，不因多个下标或同一字段多个公式而强制转图。原始图片保留；无法可靠转换的公式仅使用已绑定原始预览兜底，否则进入复核。Excel 中 `参考aaa001.png)` 对应真实图片 `001.png`，编号至少三位；难度输出易／中／难，档位保持不变。包含 Windows 可信组件来源标记的受限自动恢复。详见[导出约定](docs/export-contract.md)，客户 APP 实际导入与显示仍需验收。

v0.3.4 修复题组解析编号说明被误拆成题目导致的重复题号误报；问题样本182道实际题目全部导出，公开回归420项通过。安装包发布状态以 Release 页面为准。

v0.3.6 修复解析步骤误拆题、编号标点变化吞题和共享题组解析串题，并补充原题号的出现位置与题干提示。本地19份样本2,021个输出单元中1,923导出、98按既有表格策略跳过、0待复核；500项公开回归通过。参见[题目逻辑走查](docs/question-logic-review.md)。[v0.3.6 Release](https://github.com/javen-yan/qa-parser/releases/tag/v0.3.6) 已正式发布，三平台分发校验均通过。

v0.3.5 修复共享参考图归属、清理点睛／点精、补充特殊作答与公式兼容。全部18份本地样本1,920题中导出1,822题、98题表格策略跳过、0题待复核；特殊题正常导出到独立工作表，既有选择／填空模板保持不变。详见[特殊题型解析与验证](docs/special-question-postprocessing.md)，发布状态以 [v0.3.5 Release](https://github.com/javen-yan/qa-parser/releases/tag/v0.3.5) 为准。

## 历史 Mac 本地交付（非 v0.3.2 安装包）

当前已验证 macOS ARM64。完整解压应用 ZIP 后打开“题库转换.app”，选择题型并添加 Word，转换后可预览题目、查看未导出原因或打开结果包。

| 版本 | 本地交付 | 固定九份样本结果 |
|---|---|---|
| 轻量版 | `dist/题库转换-macOS-arm64-轻量版.zip`（40.63 MiB） | 899/900；化学填空2第62题明确列入未导出清单 |
| 原图完整版 | `dist/题库转换-macOS-arm64-原图完整版.zip`（167.08 MiB） | 900/900；额外携带 LibreOffice 原图渲染运行时 |

完整结果在 `outputs/display-final-complete/batch/最终结果/题库最终结果.zip`。程序、结果包及真实样本保留本机，不提交Git。完整结果的每份Word分别对应Excel、图片和题目预览HTML；必须完整解压，不能只拷贝HTML。详情见[Mac交付记录](docs/display-release.md)。

原图完整版只对审计通过的原始WMF进行回放，不猜测未知公式。4709个LaTeX公式的显示构造已检查，不能等同于所有公式逐项人工语义认证。两个Mac包为本机验证的独立应用，其他电脑、签名公证及客户安全策略仍需实际验证。

## 正式下载与统一发布入口（v0.3.6）

[v0.3.6 正式版本页](https://github.com/javen-yan/qa-parser/releases/tag/v0.3.6) 已发布。三平台文件已上传 OSS 并完成匿名完整下载 SHA-256 校验：

| 平台 | 下载 |
|---|---|
| Windows x64 | [便携 ZIP](https://maiya-reader.oss-cn-beijing.aliyuncs.com/qa-parser/releases/v0.3.6/windows-x64/qa-parser-v0.3.6-windows-x64-light.zip) |
| Mac Apple Silicon（ARM64） | [应用 ZIP](https://maiya-reader.oss-cn-beijing.aliyuncs.com/qa-parser/releases/v0.3.6/macos-arm64/qa-parser-v0.3.6-macos-arm64-light.zip) |
| Mac Intel（x86_64） | [应用 ZIP](https://maiya-reader.oss-cn-beijing.aliyuncs.com/qa-parser/releases/v0.3.6/macos-x86_64/qa-parser-v0.3.6-macos-x86_64-light.zip) |

[校验和](https://maiya-reader.oss-cn-beijing.aliyuncs.com/qa-parser/releases/v0.3.6/release/SHA256SUMS.txt) · [分发验证记录](https://maiya-reader.oss-cn-beijing.aliyuncs.com/qa-parser/releases/v0.3.6/release/delivery-record-windows-x64-macos-arm64-macos-x86_64.json)

完整解压对应 ZIP，Windows 进入“题库转换”文件夹双击 EXE，Mac 打开 `.app`。保留运行时和许可证。固定 WebView2、WMF 转换器、离线字体及 Python 运行时随 Windows 包分发，首次启动不下载开发环境；任务和缓存写入用户目录。

发布只使用 [Build and publish release](https://github.com/javen-yan/qa-parser/actions/workflows/release.yml) 一个入口：构建、三平台验证、OSS 上传、公网校验、Release 更新和镜像附件清理一次完成。失败后选择 `resume` 并填写已有 Release ID，复用已验证包；预算受限时可由一次性本机 runner 收尾，沿用 GitHub Secrets。旧版本仅按明确指定 ID 清理。原 Windows/Mac 单平台 workflow 仅供内部复用，旧 OSS 自动接续和独立清理入口已移除。详见[统一发布流程](docs/release-pipeline.md)。

本次三平台包对应源码 `f047c62077f9fdf49dd7897de9ac2f4ad10d12a1`；[发布运行](https://github.com/javen-yan/qa-parser/actions/runs/42944224225) 的三个原生构建及交付任务均成功。2026-11-11 UTC 正式发布，OSS 匿名完整下载 SHA-256 校验通过，旧版本保留。云端提供轻量版；客户 Windows 11、Mac 签名公证及目标 APP 导入/显示仍需验收，私有 Word 未上传。

## 输出约定

- 保持Reference两份模板的列名、顺序：选择题14列，填空/考点4列。未约定的ID、sid、questionType等编码默认空。
- 选择题保留题干、选项、答案，分析/详解按顺序合并。难度为易/中/难的稳定随机占位，可重复，不代表教学评估。
- 填空一道大题一行，题目保留空位；空位与答案有明确对应时，考点为按答案回填后的完整陈述，答案按空位顺序用 ASCII 逗号连接，单空内部标点和同义答案不拆分。
- LaTeX使用`$$…$$`；普通图片在原位置引用“参考aaa001.png)”等真实资源。可靠性不足时保留证据并明确隔离，不静默丢失。
- 自动转换交付已通过题目，并明确报告未导出数量、题号和原因；没有可导出题目时不生成伪成功Excel。低层`export`命令仍需显式`--allow-partial`才能部分导出。
- 新输出目录不得已存在，避免覆盖之前的结果。源文件快照、中间任务和技术证据放在诊断目录，不混入客户结果ZIP。

本轮按模板与约定字段核对格式，真实系统导入和APP渲染未验证，不作为本轮格式交付阻塞项。六题格式对照包留在 `outputs/import-acceptance/`。字段协议见[导出契约](docs/export-contract.md)。

## 本地开发

已验证的Mac开发环境为Python 3.13.3。当前工作目录已有`.venv`：

```sh
.venv/bin/python -m qa_parser desktop
.venv/bin/qa-parser run reference/微软公式物理.docx --kind choice --out outputs/my-choice-run
.venv/bin/qa-parser run reference/MathType公式化学填空2.docx --kind blank --out outputs/my-blank-run
```

从新检出创建核心开发环境：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python scripts/check_public_tests.py --report build/public-tests.json
node tests/test_math_preview.js
```

Mac桌面依赖见`requirements-desktop.lock`，构建入口为`scripts/build_desktop.py`；`启动题库转换.command`是本机依赖`.venv`的开发入口。Windows使用`requirements-windows.lock`及`packaging/windows-build.ps1`，不能在Windows安装Mac锁文件或复制Mac dylib。

本地临时产物整理与恢复方式见[归档说明](docs/local-artifacts.md)。

构建时需联网获取依赖，运行时不下载。Windows原生来源、固定版本和SHA在`packaging/windows-runtime-lock.json`；下载、校验或资源缺失会让构建失败，不发布伪成功包。Python包固定版本及官方包哈希；构建下载按OS/架构/锁文件缓存，命中仍校验，网络失败可重新手动运行工作流。第三方许可见[依赖说明](THIRD_PARTY_NOTICES.md)及包内licenses。

CLI还提供`parse`、`validate`、`export`、`batch`和`config`。退出码：0完整成功、2部分失败/待复核、1执行或全部失败。Web调试入口`ui`保留，桌面默认不打开系统浏览器。

## 目录

| 目录 | 用途 |
|---|---|
| `src/qa_parser/` | 共用核心、平台边界、产品UI与固定离线渲染资源 |
| `config/` | 模板映射与导出约定 |
| `tests/`、`scripts/` | 合成回归、构建和验收工具 |
| `packaging/`、`.github/workflows/` | 原生依赖清单、许可、Windows云端构建 |
| `docs/` | 技术决策与验收范围 |
| `reference/` | 本机真实样本；不入库、不上传CI |
| `dist/` | 本机最终应用包；不入库 |
| `outputs/` | 结果、关键验收证据与归档；不入库 |
| `build/`、`.venv/` | 可再生成的开发/构建目录；不入库 |

仓库不包含原始Word/Excel、原始公式WMF、客户端数据、历史诊断和构建缓存。公开合成测试会列出未执行的私有样本检查，意外跳过会报错。

可选下载镜像：见 [OSS 分发](docs/oss-delivery.md)，默认只做计划检查，实际上传需仓库 Secrets。

客户输出约定：`参考aaa001.png)` 映射真实图片 `001.png`；数字 Word 同名导出 Excel/图片文件夹；难度为易、中、难。可靠公式优先输出 `$$...$$` 文本，原始示意图保持图片。
