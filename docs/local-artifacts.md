# 本地临时产物整理（2026-11-07）

当前先建立清单，不删除文件、不移动正在使用的目录。Windows 构建和分发验证正在进行，`build/windows`、Windows 测试输出及依赖暂存保持原位。

## 本轮可归档范围

`scripts/archive_local_artifacts.py` 使用封闭候选清单，默认只生成 dry-run JSON：

| 候选目录 | 依据 | 本次磁盘占用约值 |
|---|---|---:|
| `outputs/phase1` | 第一阶段早期中间结果 | 78 MiB |
| `outputs/phase1-v2` | 第一阶段早期中间结果 | 78 MiB |
| `outputs/mac-phase1` | 第一阶段早期中间结果 | 167 MiB |
| `outputs/mac-phase1-full` | 第一阶段早期中间结果 | 820 MiB |
| `build/lib` | 可由源码重新生成的 setuptools 构建副本 | 240 KiB |
| `build/bdist.macosx-15.0-arm64` | 旧 wheel 构建暂存 | 当前为空 |

前四项已有 [第一阶段记录](phase1-validation.md) 明确由 `outputs/mac-acceptance` 作为最终记录替代；该最终记录继续保留。上述占用来自 `du`，与清单的逻辑文件字节之和可能不同。

已生成 `outputs/local-archive-plan-20261107.json`，共 6 个候选、1,145,952,703 逻辑字节。**尚未执行归档。** 当前沙箱无法读取进程清单，计划如实记录 activity inspection unavailable；这不能证明目录无人使用。

## 保留范围

- 全部 `reference/`、源码、测试、配置、图标、依赖资料、开发环境和 Git 数据。
- 全部 `dist/`，包括已验收 Mac 两个版本及 ZIP；历史发布物暂不移动。
- `outputs/display-*`：最终结果、显示与公式核验、原生窗口截图、日志和清单；`outputs/import-acceptance`。
- `outputs/mac-acceptance`、`outputs/bundled-all-nine` 等历史基线与 `outputs/分发 验证`；它们仍被报告或比较脚本使用。
- 全部 `output/`：约 8 MiB，实际是文档引用的截图和人工核验材料，不能因目录名像临时目录而清除。
- `build/desktop`、`build/windows`、Windows 本地/云端相关证据、UI 会话、用户转换结果，以及未明确列入候选的任何路径。

## 执行与恢复

重新盘点时使用新的清单路径，避免覆盖历史计划：

```sh
.venv/bin/python scripts/archive_local_artifacts.py --manifest outputs/local-archive-plan-next.json
```

检查清单中的候选、引用、内容快照和进程占用后，可由主任务执行：

```sh
.venv/bin/python scripts/archive_local_artifacts.py --apply --manifest outputs/local-archive-plan-next.json
```

脚本执行前会重新核对目录快照，并读取当前用户的进程与打开文件；检查失败或发现占用时停止。实际操作仅为同文件系统重命名，目标为 `outputs/archive/<UTC时间>/<原相对路径>`，不会删除内容，也不会覆盖已存在目录。每次移动写入 receipt，记录源路径、归档路径和恢复办法。若中断，先查看 receipt，不要直接重复执行旧计划。

归档只是把旧内容收拢，**不会释放磁盘空间**。需要释放空间时，应另行决定保留周期和删除范围。本次未扩大到删除、打包压缩或清理私有题库。
