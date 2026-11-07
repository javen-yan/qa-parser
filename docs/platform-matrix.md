# 原生架构、缓存及发布矩阵

| 目标 | 原生云端标签 | 本轮包 | 当前证明边界 |
|---|---|---|---|
| Windows x64 | windows-2022 | 固定WebView2+原生WMF轻量便携ZIP | 逐轮CI记录，不能代替客户Windows11验收 |
| macOS Apple Silicon | macos-15（ARM64） | 原生arm64轻量.app ZIP | Mac本地已验证；云端另行原生构建/运行 |
| macOS Intel | macos-15-intel（x86_64） | 原生x86_64轻量.app ZIP | 新增云端门槛，未运行前不承诺支持 |
| Windows 32位x86 | 未设发布runner | 只评估，不分发 | Python/浏览器有可用组件；WMF i686闭包及完整运行未验证 |
| Windows ARM | 不在本次范围 | 无 | 无支持承诺 |

标签按[GitHub官方当前架构表](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)选择，不使用latest猜测架构。Mac构建时核对uname/Python、所有Mach-O，Windows核对PE x64；Mac拒绝Rosetta代替原生。

## 缓存

仅缓存构建期Python下载与原生来源压缩包，不缓存用户题库、token、诊断任务、虚拟环境或已冻结程序。

- Windows key包含windows-2022、runner OS/架构、Python3.13.3及对应requirements/runtime锁哈希。
- Mac key包含macOS15、arm64/x86_64、Python3.13.3和requirements/runtime/builder哈希。
- 不设置跨架构restore前缀；Python安装仍用require-hashes，原生下载每次读取前校验锁内SHA，命中缓存不会绕过检查。
- 构建产物每次重新生成；修改锁或构建器会换key。客户端启动不下载依赖。

## 发布约定

普通平台CI仅手动执行，不发布Release。版本tag触发完整三个原生目标；tag由用户创建，不由工具猜首个正式版本。只有release相关job具有contents:write。

完整性检查包括真实解压后的同一ZIP、核心/冻结程序结果、WMF与窗口、资源字节及运行前后程序目录。三个架构任一失败，不公开成功Release。手动候选流程只创建未发布的草稿，不冒充正式版本。

正式tag需与pyproject版本一致，且`packaging/release-policy.json`记录正式验收通过；当前stable_release_approved=false。预发布同样要求完整架构和运行证据。发布说明含版本、架构、ZIP、字节数、SHA256、使用方式及限制。

GitHub Actions artifact配额不足不等于构建能力失败。发布流程可在原生release构建job中直接上传同一草稿Release，再由最终job核对并发布；不依赖Actions artifact中转，不使用OSS。草稿失败时保持未发布，不能覆盖已有正式版本。

云端首版均为轻量包。Mac本地原图完整版和900/900证据独立保留；云端不携带可选LibreOffice，也不上传九份真实Word。Windows客户环境、Mac签名公证及系统导入仍需独立记录。
