# macOS 原生云端构建

`.github/workflows/macos-portable.yml`支持手动触发和`workflow_call`，由发布编排调用时仍只有contents:read权限。ARM64和Intel独立执行，不能用交叉编译或Rosetta结果替代原生运行。

| 产物 | runner标签 | 原生架构 | 本轮变体 |
|---|---|---|---|
| macOS M系列 | `macos-15` | arm64 | 轻量候选 |
| macOS Intel | `macos-15-intel` | x86_64 | 轻量候选 |

标签依据2026-11-07核对的[GitHub runner官方表](https://github.com/actions/runner-images)及[runner选择文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)。不使用macos-latest；macOS14已从GitHub Actions退役。固定标签仍会更新镜像工具，运行记录保存实际OS、Python、架构和源码提交。

## 与本机已验收版本的关系

保留`scripts/build_desktop.py`及已有Mac交付文件。本轮独立的`scripts/macos_ci.py`在每个架构上从固定源码编译libwmf0.2.13与原Mac补丁、FreeType2.13.3、libpng1.6.48、libjpeg-turbo3.1.0，再使用相同产品核心/UI冻结应用。系统expat/zlib和WebKit是macOS组成部分，不从另一台机器复制。不会直接安装当前Homebrew最新版或把ARM64 dylib混入Intel包。

云端包只属于轻量变体，不包含可选LibreOffice。现有Mac原图完整版900/900是先前本机真实样本证据，不能迁移为云端ARM64/Intel900题承诺。云端最低目标设置为macOS15.0，较旧系统不在本轮验证范围。应用为ad-hoc签名，未取得Developer ID签名、公证或客户电脑验收。

## 固定来源与缓存

- `requirements-macos.lock`固定Python版本依赖及官方PyPI SHA；构建后端单独bootstrap且禁止未锁定build isolation下载。
- `packaging/macos-runtime-lock.json`固定4个原生上游源码及补丁SHA。已有归档通过仓库分发，其余下载到专用缓存；缓存命中仍逐个校验。
- 缓存key包含macOS15、架构、Python3.13.3、全部Python锁文件、原生锁文件和构建脚本摘要。没有跨架构restore前缀，不缓存`.venv`、应用包、任务、令牌、真实Word或用户目录。
- 只缓存pip下载资料和原生源码归档；重新安装/编译前校验，既不信任缓存中的运行库，也不以命中缓存跳过原生冒烟。
- 完整对应源码、补丁、原生库许可和Python许可进入包内licenses。下载/校验/构建失败会终止，不以缺依赖跳过当作成功。

## 发布门槛

公开合成回归→构建→架构与原生库依赖检查→ZIP解压至中文空格路径→冻结CLI与开发核心语义比较→原生WMF→Cocoa真实窗口字体/公式检查→程序目录未写入检查。产物就是经过上述解压运行的同一ZIP，附SHA256与平台JSON。

`qa-parser-macos-arm64-candidate`与`qa-parser-macos-x86_64-candidate`分别上传，14天保留；检查日志为独立evidence artifact。发布编排应等待两个Mac架构及Windows全通过后统一处理，不得用单平台成功发布残缺正式版本。这里不自行创建任何版本tag或正式Release。
