# Word 题库转换工具

离线读取 DOCX 中的选择题或填空题，生成 Excel、图片及可离线查看的题目预览。采用同一 Python 解析核心、CLI 和独立桌面窗口，客户无需安装 Python、Office、MathType、Node 或开发工具。

## Mac 使用与当前交付

当前已验证 macOS ARM64。完整解压应用 ZIP 后打开“题库转换.app”，选择题型并添加 Word，转换后可预览题目、查看未导出原因或打开结果包。

| 版本 | 本地交付 | 固定九份样本结果 |
|---|---|---|
| 轻量版 | `dist/题库转换-macOS-arm64-轻量版.zip`（40.63 MiB） | 899/900；化学填空2第62题明确列入未导出清单 |
| 原图完整版 | `dist/题库转换-macOS-arm64-原图完整版.zip`（167.08 MiB） | 900/900；额外携带 LibreOffice 原图渲染运行时 |

完整结果在 `outputs/display-final-complete/batch/最终结果/题库最终结果.zip`。程序、结果包及真实样本保留本机，不提交Git。完整结果的每份Word分别对应Excel、图片和题目预览HTML；必须完整解压，不能只拷贝HTML。详情见[Mac交付记录](docs/display-release.md)。

原图完整版只对审计通过的原始WMF进行回放，不猜测未知公式。4709个LaTeX公式的显示构造已检查，不能等同于所有公式逐项人工语义认证。两个Mac包为本机验证的独立应用，其他电脑、签名公证及客户安全策略仍需实际验证。

## 云端构建与下载（Windows / Mac双架构）

Mac使用 [macOS native portable candidates 工作流](https://github.com/javen-yan/qa-parser/actions/workflows/macos-portable.yml)，分别在M系列ARM64和Intel x86_64原生构建。云端只提供轻量版，不能替代本地原图完整版900/900结论。架构、缓存及版本发布约定见[平台矩阵](docs/platform-matrix.md)。

仓库的 [Windows portable candidate 工作流](https://github.com/javen-yan/qa-parser/actions/workflows/windows-portable.yml) 仅手动触发。在 Actions 中选择该工作流 → Run workflow。只有构建、合成回归、解压后冻结程序及原生窗口冒烟均通过，才上传 `qa-parser-windows-x64-candidate` artifact；证据另存 `windows-build-evidence`，保留14天。

下载artifact，先解开GitHub外层归档，再完整解压其中的 `qa-parser-windows-x64-candidate.zip`，进入“题库转换”文件夹双击EXE。保留 `_internal` 及其子目录。固定版WebView2、WMF转换器、离线字体和Python运行环境均随包分发；不在首次启动时下载。任务及浏览器缓存写入用户LOCALAPPDATA，不写程序目录。

**各平台云端包目前是候选构建。** CI运行环境是Windows Server 2022 x64；客户Windows11 x64的断网、非管理员、只读程序目录、安全软件及签名验收需分别记录。真实9份Word未上传，不能把合成测试通过写成Windows 900/900。Windows暂不启用Mac完整版的LibreOffice原图路径，复杂原图可能进入未导出清单。详见[Windows构建及验证说明](docs/windows-ci.md)。

## 输出约定

- 保持Reference两份模板的列名、顺序：选择题14列，填空/考点4列。未约定的ID、sid、questionType等编码默认空。
- 选择题保留题干、选项、答案，分析/详解按顺序合并。难度为低/中/高的稳定随机占位，可重复，不代表教学评估。
- 填空一道大题一行，保留公共题干、小问及完整答案，考点默认空。
- LaTeX使用`$$…$$`；普通图片在原位置引用“请参考0001.png”等真实资源。可靠性不足时保留证据并明确隔离，不静默丢失。
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
