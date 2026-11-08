# 可选 OSS 下载分发

`oss-sync.yml` 只从本仓库已验证的 GitHub Release 下载二进制，不使用 Actions artifacts，默认计划模式不改 Release；实际同步可在完整验证后保留 Release 下载页面并清理已镜像附件。默认 `plan_only=true` 只检查来源、目标元数据与路径；不上传，也不下载大 ZIP。实际同步会下载 ZIP，核对 GitHub SHA-256、原生验证记录和 ZIP CRC，然后上传。

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

三平台原生构建与汇总已通过，最新完整候选 Release ID 为 `438035464`。此前 Windows 单平台候选 `438026570` 已完成 OSS 上传及匿名完整下载 SHA 校验。真实客户环境、签名公证和系统导入仍未验收。

GitHub 草稿可见性和附件清理需要 `contents: write`，检查生产任务需要 `actions: read`；checkout 不保留凭据。参见 https://docs.github.com/en/rest/releases/releases 。

## 保留 Release 页面，清理镜像附件

`cleanup_draft` 是兼容保留的输入名，现语义为：先验证所有 OSS 文件的匿名下载 SHA，保存带平台下载链接、大小、校验值和候选说明的 Release body，重新读取确认成功后，仅删除已镜像附件。最后把本次手动候选置为 `prerelease=true`、`draft=false`、`make_latest=false`；保留 Release 页面、候选源码 tag 和自动 Source 链接，绝不删除整条 Release、不冒充稳定正式版。仓库可见性不变。

缺资产、未知附件、校验失败、生产任务运行中或说明未保存时不删附件。按 Release ID 串行；中途停止可基于已保存 receipt 恢复，只处理剩余附件。已有稳定或无本工具镜像标记的已发布 Release 不处理。公开 `release/delivery-record-<targets>.json` 保存来源与校验记录。

独立恢复：`python scripts/cleanup_oss_draft.py --release-id ID --receipt delivery-record.json` 仅验证，追加 `--apply` 才保存说明、清理附件并保留候选预发布页面。仅允许本项目及 maiya-reader 北京下载域名。历史空/失败草稿不批量处理。Release 附件与 Actions artifact 配额不同，清理不证明 artifact 配额恢复。

大于 16 MiB 的对象采用 4 路、8 MiB 分片；每片 Content-MD5 与 SDK CRC 校验，合并时核对组合 CRC 并再次带 forbid-overwrite，失败只终止本次 upload ID。小文件继续单次上传。需额外只读 `oss:GetBucketVersioning` 和本目录 `oss:AbortMultipartUpload` 权限；若 bucket 版本控制已启用或暂停，OSS 会忽略 forbid-overwrite，因此脚本拒绝写入，不修改版本控制。依据：[分片上传](https://www.alibabacloud.com/help/en/oss/developer-reference/use-oss-python-sdk-to-multipart-upload)、[完成分片与禁止覆盖条件](https://www.alibabacloud.com/help/en/oss/developer-reference/completemultipartupload)。

CI 在 Release-ID 串行锁内，仅列举并终止当前精确对象 key 的未完成旧分片，再启动新上传。需 `oss:ListMultipartUploads` 和 `oss:AbortMultipartUpload`；不会清理相邻 key、其他候选或完整 OSS 对象。
