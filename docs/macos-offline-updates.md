# macOS 离线签名更新

从下一个 macOS 版本开始，分发 ZIP 不再只有单个 `.app`，而是一个文件夹：

```text
题库转换/
  题库转换.app
  管理更新.app
  install-state.json
  update-manifest.json
  update-manifest.sig
  使用说明.txt
```

v1.0.0 用户仍需手动替换一次完整包；从包含本机制的版本开始，后续版本可以在不联网的情况下离线更新和回退。

## 客户使用

首次完整解压 ZIP 后双击“题库转换.app”。不要只复制单个 app，`管理更新.app` 和 `install-state.json` 必须保留。

更新：关闭所有题库转换窗口，等待转换结束，双击“管理更新.app”，选择新的完整分发 ZIP。也可以直接在程序窗口点击“离线更新”，程序会退出、启动管理器并完成安装，然后自动重新打开。管理器会校验签名、文件清单、原生架构和代码签名，再原子替换当前 app。更新成功后保留上一版本；如果新版本启动失败，自动恢复上一版本。

回退：再次打开“管理更新.app”，选择“恢复上一版本”。回退同样需要关闭所有窗口。

更新源就是后续版本的普通分发 ZIP，不需要额外的 `.qaupdate` 文件；管理器也接受 `.qaupdate` 后缀的同一 ZIP 内容。每次下载完整包，不做二进制差分。

## 签名与校验

`update-manifest.json` 位于分发根目录，列出 `题库转换.app` 内除 `Contents/_CodeSignature/` 外的所有文件和符号链接：

- 常规文件：长度、SHA-256、权限位
- 符号链接：相对目标，必须解析在 app 内
- 平台目标、产品版本、协议版本

清单由 Ed25519 私钥签名，签名文件为 `update-manifest.sig`；仓库 `packaging/update-public-key.txt` 固定公钥，Windows 和 macOS 共用同一把发布密钥。管理器在构建时把公钥编入自身，不从安装目录读取。

安装前依次检查：

1. ZIP 路径、重复路径、加密标志、特殊文件、符号链接越界
2. 清单 Ed25519 签名和规范化 JSON
3. app 文件/符号链接与清单完全一致
4. `Contents/Info.plist` 版本与清单版本一致
5. `codesign --verify --deep --strict`
6. 主程序 `lipo` 架构与当前 Mac 匹配
7. 新版本必须高于当前版本；降级只走“恢复上一版本”

签名清单放在 app 外部，避免“签名后再写清单”破坏 app 的代码签名封条。`codesign` 仍负责 app 自身的 bundle 完整性；在取得 Developer ID 后，后续版本需要先签名、公证并 staple，再生成清单。

## 更新事务

管理器独占 `operation` 锁；替换期间独占 `activity` 锁。业务 app 启动时如果检测到同目录的 `install-state.json`，会持有共享 `activity` 锁，因此运行中的 app 不会被替换。

流程：

1. 解压到 `.update-staging`，保留符号链接和权限
2. 校验新 app 和清单
3. 当前 app 及根清单移入 `.versions/v<当前版本>/`
4. 新 app 和清单移到根目录
5. 写 `install-state.json`：`current`、`previous`、`pending`
6. 启动新 app，等待页面就绪文件 `.ready-<nonce>.json`
7. 成功则清除 `pending`；失败则切回上一版本

下次打开管理器发现未完成的 `pending` 时，会先恢复上一版本。转换任务失败不会触发回退，只有启动确认失败才回退。`~/Library/Application Support/QA Parser` 下的任务、缓存和输出不受影响。

## 构建与验证

- `scripts/macos_ci.py` 构建并 ad-hoc 签名 app，`Info.plist` 版本来自 `pyproject.toml`
- `scripts/build_macos_distribution.py` 生成根清单、签名、独立 `管理更新.app`、`install-state.json`，并打出最终候选 ZIP
- `scripts/macos_update_smoke.py` 用真实候选 ZIP 做一次签名升级、启动和回退验证，输出 `update-validation.json`
- `candidate_metadata.py` 把更新证据绑定到 ZIP 的 SHA-256 和字节数
- `prepare_release.py` 对 v1.1 及以后要求 macOS 更新证据，缺失时拒绝发布

CI 需要 `QA_UPDATE_PRIVATE_KEY_HEX`（仓库 Secret）和 `QA_UPDATE_PUBLIC_KEY_HEX`（仓库变量）。私钥不进入源码、构建参数、更新包或日志。原生验证在 macOS ARM64 和 Intel 两个 runner 上分别执行。

## 限制

- 当前仍是 ad-hoc 签名，`codesign` 只能证明 bundle 完整性，不能证明发布者身份；Gatekeeper 和客户安全策略仍需 Developer ID 签名 + 公证。
- 管理器本身暂不自更新；更新管理器需要手动替换完整包，后续可以增加“下次启动更新管理器”的暂存机制。
- 如果程序在 `/Applications` 且当前用户无写权限，更新会失败；需要把完整文件夹放到用户可写目录，或提供管理员授权流程。
- ARM64 和 x86_64 包不通用；更新器拒绝架构不匹配。
- 客户 Mac 实机、目标系统导入显示和 Developer ID 公证仍未验收。
