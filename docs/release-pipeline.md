# 统一发布入口

GitHub Actions 只保留一个人工发布入口：`release.yml`（Build and publish release）。Windows/macOS 独立 workflow 仅供内部复用，不再单独手动触发；旧 OSS 自动接续和独立清理 workflow 已移除，避免重复发布。

## 正常发布

推送经过授权的新版本 tag 后，自动完成：建立私有草稿 → 三平台原生构建和验证 → 汇总验证附件 → OSS 上传 → 所有对象匿名完整下载 SHA-256 校验 → 保存下载链接 → 清理已镜像附件并发布 Release。

手动 `operation=build` 用于 main 的候选版本，不凭空创建正式版本号。正式版仍由原始 tag、源码版本与发布政策决定。成功后原始题库、开发环境和密钥不进入客户包。

## 失败恢复

选择同一入口的 `operation=resume`，填写原 `release_id`。prepare、Windows、Mac 构建全部跳过；只核实原生产 run 的仓库、源码、tag、原生任务和每个执行步骤，再复用附件继续。

原生任务失败时拒绝恢复发布。允许原生任务全部成功但后续交付失败；记录原 run 的真实失败状态，不把它改成 success。汇总使用原源码提交中的版本、发布政策和说明。下载后的 ZIP 必须通过 GitHub 摘要、原生元数据及 CRC 校验。

没有完整 OSS receipt 时继续上传并校验；有 receipt 时重新完整下载校验、补齐页面和剩余附件清理。正式 Release 在 OSS 成功前保持草稿。未知附件、来源变化、内容不一致、并发生产任务或公开下载失败均停止清理。

`retire_ids` 可选，必须显式填写获授权的旧 Release ID。新版本完成交付后才退休这些记录；保留旧源码 tag 和 OSS 对象，不自动扩大删除范围。

## 本机收尾

Actions 托管预算受限时使用 `delivery_runner=local`。一次性 runner 仅处理该收尾 job，GitHub Secrets 直接注入 job 环境，不输出、不写入脚本或客户包。macOS ARM64 本机执行：

```bash
python3 scripts/run_local_release.py --release-id 438806506 --retire-ids 438667829,438798359,438800027 --evidence outputs/release-v0.3.3/local-recovery
```

启动器验证官方 runner SHA，临时注册独有名称、专用 label、ephemeral runner，不安装服务；自动调度统一入口的 resume，结束后注销并删除临时目录。原生包不重建。GitHub self-hosted runner 当前免 Actions 使用费；OSS 流量/存储沿用现有账户规则，不承诺云存储免费。

运行前需要本机 GitHub 管理权限、GitHub 网络和现有仓库 OSS Secrets。首次出现预算/网络错误不应删除已有草稿或重打源码 tag。

v0.3.3 原始源码为 `012f57b88a9e5269d8ddb048fdc9ed0a55acd3e3`，生产 run `42574069235`，已有草稿 `438806506`。本次基础设施整合不改变这些身份，也不替代客户环境和目标 APP 验收。

## v0.3.3 收尾结果

[运行 42581561779](https://github.com/javen-yan/qa-parser/actions/runs/42581561779) 已成功完成：三平台包及说明公开 SHA 校验通过，Release 438806506 为正式发布且镜像附件为零，指定旧 Release 记录已不在清单。旧源码 tags、OSS 历史对象及仓库私有状态保留；一次性本机 runner 已注销、临时目录已删除。原生生产 run 42574069235 的预算失败状态原样保留，发布依据是其中三个成功的原生任务及原始包摘要，不伪造整个原运行成功。

## v0.3.5 发布结果

2026-11-11 03:07 UTC，Release `441157129` 正式发布（非草稿、非预发布）。源码标签 `v0.3.5` 对应 `f047c62077f9fdf49dd7897de9ac2f4ad10d12a1`，发布运行 [42944224225](https://github.com/javen-yan/qa-parser/actions/runs/42944224225) 的 prepare、Windows x64、Mac ARM64、Mac Intel 和 deliver 全部成功。

三平台安装包、说明、校验清单和分发记录已上传 OSS 并完成匿名完整下载 SHA-256 校验；GitHub 已镜像附件按既有流程清理，正式下载链接保留在 [v0.3.5 Release](https://github.com/javen-yan/qa-parser/releases/tag/v0.3.5)。旧 Release、源码 tag 与 OSS 对象保留。

本地发布前485项公开回归通过；18份原始 Word 的1,920题导出1,822题，98题表格策略跳过，0题待复核。客户环境及新增特殊题型工作表的目标系统导入仍未验收。此次云端交付耗时主要为 Windows 大包到 OSS 的分片上传，未修改上传策略或绕过下载校验。

本机证据保存在 `output/release-v0.3.5/release.json` 与 `output/release-v0.3.5/run.json`；这些运行证据不提交源码仓库。
