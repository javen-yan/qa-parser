# 统一发布入口

GitHub Actions 只保留一个人工发布入口：`release.yml`（Build and publish release）。Windows/macOS 独立 workflow 仅供内部复用，不再单独手动触发；旧 OSS 自动接续和独立清理 workflow 已移除，避免重复发布。

## 正常发布

推送经过授权的新版本 tag 后，自动完成：建立私有草稿 → 三平台原生构建和验证 → 汇总验证附件 → OSS 上传 → 所有对象一次匿名完整下载 SHA-256 校验 → 保存下载链接 → 清理已镜像附件并发布 Release。

手动 `operation=build` 用于 main 的候选版本，不凭空创建正式版本号。正式版仍由原始 tag、源码版本与发布政策决定。成功后原始题库、开发环境和密钥不进入客户包。

## 失败恢复

选择同一入口的 `operation=resume`，填写原 `release_id`。prepare、Windows、Mac 构建全部跳过；只核实原生产 run 的仓库、源码、tag、原生任务和每个执行步骤，再复用附件继续。

原生任务失败时拒绝恢复发布。允许原生任务全部成功但后续交付失败；记录原 run 的真实失败状态，不把它改成 success。汇总使用原源码提交中的版本、发布政策和说明。下载后的 ZIP 必须通过 GitHub 摘要、原生元数据及 CRC 校验。

没有完整 OSS receipt 时继续上传并校验；有 receipt 时重新完整下载校验、补齐页面和剩余附件清理。

常规发布中，上传函数成功返回后，将同一进程内刚完成的完整下载校验结果传给收尾；收尾仅再次检查公开 HEAD 的长度与 SHA-256 元数据，不再把所有包下载第二遍。该复用标志不写入 receipt，也没有命令行跳过开关；恢复发布或单独执行清理仍做一次完整下载校验。上传 CRC、GitHub 附件摘要、原生构建验证及源码绑定保持不变。显式退休历史版本的独立校验不属于常规发布提速范围。正式 Release 在 OSS 成功前保持草稿。未知附件、来源变化、内容不一致、并发生产任务或公开下载失败均停止清理。

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


## v0.3.6 发布结果与校验提速

2026-11-11 06:23 UTC，[v0.3.6](https://github.com/javen-yan/qa-parser/releases/tag/v0.3.6) 正式发布，Release ID `441279624`，非草稿、非预发布。标签源码为`b835b2566e202eb62fedaae2e2b67274092d3a1d`；[运行42966915595](https://github.com/javen-yan/qa-parser/actions/runs/42966915595)全部成功。Windows构建5分24秒、Mac ARM64构建2分10秒、Mac Intel构建4分54秒，交付阶段40分25秒。三平台OSS文件均已完整下载校验，旧发布保留。

日志显示Windows ZIP上传从05:43:50持续到06:10:52，约27分钟；随后完整下载SHA校验约13秒。主要瓶颈是上传而不是本地校验或公开下载。原流程收尾再次完整下载；按用户要求，main提交`4c61e88`改为同次发布复用第一次完整校验、收尾仅HEAD核对，恢复任务仍完整校验。该优化505项公开测试通过、79项明确排除；本次v0.3.6已启动的固定流程仍使用旧逻辑，下次发布生效，不改写版本标签。

发布前产品回归500项通过；19份真实文档2,021个输出单元，1,923导出、98策略跳过、0待复核。客户系统导入与客户实机验收仍未完成。证据保存在`output/release-v0.3.6/`（源码仓库不提交运行证据）。

## v1.0.0 发布结果

2026-11-11 09:17 UTC，[v1.0.0](https://github.com/javen-yan/qa-parser/releases/tag/v1.0.0) 正式发布，Release ID `441411262`，非草稿、非预发布，GitHub 镜像附件已清理为零。标签源码为 `61b26adfbae1bfbf9e8246d17ace04e899b3d23a`；[运行 42987346174](https://github.com/javen-yan/qa-parser/actions/runs/42987346174) 的 prepare、Windows x64、Mac ARM64、Mac Intel 和 deliver 全部成功。

发布前 `Native distribution validation` [运行 42980990787](https://github.com/javen-yan/qa-parser/actions/runs/42980990787) 已在 `b83c230` 通过三平台原生检查：Windows 签名升级、启动失败回退、运行时摘要不变，以及 Mac ARM64/Intel 原生构建与窗口检查。`packaging/release-policy.json` 据此批准稳定发布；批准提交 `61b26ad` 只修改发布政策和说明，正式包仍由 tag 源码重新原生构建。

三平台安装包、Windows 业务更新包与运行时 ZIP 已上传 OSS，并完成匿名完整下载 SHA-256 校验。文件大小：Windows x64 `507,148,722` 字节，Mac ARM64 `45,634,556` 字节，Mac Intel `47,110,784` 字节。下载与校验清单位于 [v1.0.0 Release](https://github.com/javen-yan/qa-parser/releases/tag/v1.0.0)。

本次构建耗时约 9 分钟，交付阶段约 39 分钟。正常发布流程复用上传时的首次完整校验，收尾只核对公开对象 HEAD 的长度和 SHA-256，不再重复下载 507 MB Windows 包；恢复发布和独立清理仍做完整下载校验。上传仍是主要瓶颈，本次没有跳过校验。

发布前 527 项公开回归通过、79 项明确排除。客户 Windows 11 实机、macOS Developer ID 签名公证及目标系统导入/显示仍未验收，私有 Word 未上传。

## macOS 更新证据（v1.1 起）

macOS 候选 ZIP 从 v1.1 起包含 `题库转换.app`、独立的 `管理更新.app`、`install-state.json` 以及根目录的签名更新清单。原生构建会额外执行一次真实签名升级、启动确认和回退，输出 `update-validation.json`。

`candidate_metadata.py --macos-update` 要求证据状态为 `passed`，且 `native_macos`、`signed_upgrade`、`rollback` 均为真，并把候选 ZIP 的 SHA-256 与字节数写入 `release-metadata.json`。`prepare_release.py` 对 v1.1 及以后强制检查该证据；缺失时拒绝组装 Release。管理器自更新仍不在本轮范围，需要手动替换完整包。
