# 可选 OSS 下载分发

`oss-sync.yml` 只从本仓库已验证的 GitHub Release 下载二进制，不使用 Actions artifacts，默认计划模式不改 Release；实际同步可在完整验证后清理本次拥有的临时草稿。默认 `plan_only=true` 只检查来源、目标元数据与路径；不上传，也不下载大 ZIP。实际同步会下载 ZIP，核对 GitHub SHA-256、原生验证记录和 ZIP CRC，然后上传。

在仓库 Settings → Secrets and variables → Actions 配置以下 Repository Secrets：

- `ALIYUN_OSS_ACCESS_KEY_ID`
- `ALIYUN_OSS_ACCESS_KEY_SECRET`
- `ALIYUN_OSS_BUCKET`
- `ALIYUN_OSS_REGION`（例如 `cn-beijing`）
- `ALIYUN_OSS_ENDPOINT`（例如 `https://oss-cn-beijing.aliyuncs.com`）
- `ALIYUN_OSS_PUBLIC_BASE_URL`（现有匿名可访问的 HTTPS 下载域名）

凭据仅用于构建端，不打入客户程序。使用限定到 bucket 内 `qa-parser/*` 的最小 GetObject / PutObject 权限；脚本不删除对象、不修改 ACL，不读取旧项目。请在 GitHub Secrets 中填写密钥，不发到聊天或提交到代码。

手动选择现有 Release 数字 ID、架构及 `plan_only=false`。已通过的 Windows 可单独同步，不要求未完成的 Mac 同时发布；`all` 要求三个架构均有通过记录。候选使用 `qa-parser/candidates/<candidate-sha-run>/<architecture>/`，正式用户版本使用 `qa-parser/releases/<version>/<architecture>/`。没有 `latest`，不发明正式版本。目录包含 ZIP、原生元数据、SHA256SUMS.txt 和说明。

上传带 Content-MD5、SDK CRC 校验及 `x-oss-forbid-overwrite`。同名已存在时核对实际内容 SHA-256，内容不同则失败，不覆盖。完成后匿名完整下载每个对象并核对 SHA-256 与长度；不可公开访问时报告失败，不自动扩大权限。日志和 Actions summary 给出成功链接，不依赖 artifact 存储。不能把 plan-only 成功当作上传成功。

当前 Windows 已验证候选的 Release ID 为 `438026570`。Mac 原生启动检查仍在修复；真实客户环境、签名公证和系统导入仍未验收。六项 Secrets 已确认配置，首个 Windows 上传正在执行；以实际传输与公网校验结果为准。

GitHub 草稿可见性要求 push 权限，因此此手动工作流的 GITHUB_TOKEN 使用 `contents: write`；同步对 GitHub 只发 GET，显式开启清理后仅删除已完整镜像的手动草稿；不发布 Release，不删除源码、tag 或正式 Release，checkout 不保留凭据。参见 https://docs.github.com/en/rest/releases/releases 。

## 草稿清理

实际同步默认启用 `cleanup_draft`，计划模式不会删除。仅生产工作流已结束、所有当前资产均有 OSS 完整下载 SHA 证明时，才允许清理同一 owner/commit/run 的手动 draft。上传途中、缺资产、未知新增附件、校验失败、已发布 Release 都保留。删除前重新检查资产集合及生产工作流状态。工作流按 Release ID 串行；公开 `release/delivery-record-<targets>.json` 保留下载链接、文件哈希、来源提交及运行号，GitHub 草稿删除不影响 OSS 下载。

独立恢复命令 `python scripts/cleanup_oss_draft.py --release-id ID --receipt delivery-record.json` 仅验证；追加 `--delete` 才删除。已不存在返回幂等状态。清理仅允许本项目及已授权的 maiya-reader 北京下载域名。失败或历史空草稿不批量删除。Draft Release 附件和 Actions artifact 配额属于不同存储；清理不证明 artifact 配额恢复。
