# 原生发布与手动草稿

`.github/workflows/release.yml`直接使用GitHub Release资产传递三个原生平台的ZIP和验证元数据，不使用Actions artifact上传/下载。它解决当前artifact存储额度对传递产物的阻碍，但不绕过GitHub Release自身的权限、单文件大小或服务限制；失败会明确停止，不转存OSS。

## 两种入口

- 手动`workflow_dispatch`：只创建`candidate-<源码SHA前12位>-<run_id>`的新草稿，初始`draft=true`、`prerelease=true`。三个目标完成后补充校验清单和说明，仍保留草稿，不生成正式版本号，不自动发布。私有仓库中仅有对应访问权限的用户可查看和下载草稿资产。
- 用户推送`v*`标签：仅接受与`pyproject.toml`一致的`vX.Y.Z`或`vX.Y.Z-rc.N/beta.N/alpha.N`。正式发布还必须通过`packaging/release-policy.json`的稳定版门槛；当前门槛未放行。脚本不会替用户创建正式标签或修改已有标签。

## 流程与权限

准备任务创建一个新草稿，正文包含仓库、完整源码SHA、workflow run id、模式和标签的所有权标记。若该标签已有Release，立即失败，不接管、覆盖或删除它。正式标签在准备和发布前都递归解析至commit，必须仍指向本次源码。

Windows x64使用`windows-2022`；Mac ARM64使用`macos-15`；Mac x86_64使用`macos-15-intel`。它们调用与普通CI相同的本地composite actions，传入`upload-artifacts: 'false'`，实际执行回归、冻结程序和原生窗口检查。控制任务的runner不产生Linux产品，本流程不构建Linux包。

每个原生任务成功后向同一个草稿上传：

- `qa-parser-<target>-candidate.zip`
- `release-metadata-<target>.json`

上传前核对commit、目标、轻量版标识、实际ZIP SHA、原生冒烟和源码闭包状态；上传后检查GitHub返回的资产SHA256、大小和状态。相同名称只允许内容完全相同的幂等重试；不同内容不会覆盖已有资产。

工作流默认`contents: read`；只有此发布流程的准备、原生产物上传和最终化任务拥有`contents: write`。checkout不持久化凭证，`GH_TOKEN`仅传给Release操作步骤，不写入命令参数或文件。普通构建流程保持只读权限。

## 最终化门槛

最终任务只在Windows和两个Mac原生任务均成功后执行。它从本次拥有的草稿下载三组ZIP/JSON，逐项复核服务器和本地SHA，恢复为每个目录的`release-metadata.json`，再调用`prepare_release.py`的共享候选校验。

手动模式生成`SHA256SUMS.txt`、`release-manifest.json`和`RELEASE_NOTES.md`，完成后仍是草稿。标签模式调用原有`assemble`生成用户标签对应的版本化ZIP及说明；全部最终资产与候选资产的远端SHA再次一致后，才将同一个草稿改为发布状态。不会调用另建Release的`publish_release.py`。原始候选ZIP和目标元数据保留作输入证据；标签发布另有版本化ZIP，未删除或替换原始资产。

缺任一目标、错误源码SHA、源闭包不完整、ZIP损坏、未知资产、所有权变化或上传失败，均保留未完成草稿，不发布部分结果。已发布Release不能被此脚本再次修改。完整重跑准备任务会因草稿已存在而失败；应发起新的手动run生成新草稿，或只重跑同一run的失败后续任务。旧草稿保留供人工检查，本流程不自动清理。

## 本地检查与证据边界

脚本已通过Python语法检查，并使用不联网的模拟GitHub API验证8个所有权、完整性和最终化场景：拒绝同名Release、拒绝不完整三组资产、拒绝其他run草稿、手动完成仍为draft、标签完成才publish；相同摘要资产重试不覆盖。

这些检查不替代真实GitHub API、三平台原生构建或客户环境验收。每次实际运行以Actions日志、草稿资产及`release-manifest.json`为准。真实9份Word不进入仓库或CI，不据合成检查声明跨平台900/900。Windows x86仍是次级评估，Windows ARM不在范围；没有可选LibreOffice云端完整版承诺。

官方接口依据：[Release创建/修改](https://docs.github.com/en/rest/releases/releases)、[Release资产上传/摘要/下载](https://docs.github.com/en/rest/releases/assets)。下载使用GitHub CLI的认证API路径，以支持私有草稿，不将访问令牌拼入下载URL。
