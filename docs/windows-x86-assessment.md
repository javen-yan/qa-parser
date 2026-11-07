# Windows x86 次级可行性评估（2026-11-07）

结论：x86有可调查的依赖来源，尚无充分依据承诺交付。当前必须完成的目标仍是Windows x64；Windows ARM不在本次范围。本次只读取依赖元数据、包内文件及官方下载响应，没有修改架构配置、构建x86程序或运行x86验收。

## Python与打包链

按当前`requirements-windows.lock`的28个固定版本，对照本次从官方PyPI版本JSON取得的文件列表，用`packaging.tags`检查CPython 3.13 / `win32`兼容标签：27项存在标签兼容的wheel，`proxy_tools==0.1.0`只有sdist，仍需在x86环境验证既有固定构建后端。文件标签存在不等于DLL加载、冻结打包或桌面功能已通过。

| 原生组件 | 已找到的x86候选文件 |
|---|---|
| Pillow 12.3.0 | `pillow-12.3.0-cp313-cp313-win32.whl` |
| lxml 6.1.3 | `lxml-6.1.3-cp313-cp313-win32.whl` |
| cffi 2.0.0 | `cffi-2.0.0-cp313-cp313-win32.whl` |
| PyInstaller 6.22.3 | `pyinstaller-6.22.3-py3-none-win32.whl` |
| zstandard 0.25.0（构建期） | `zstandard-0.25.0-cp313-cp313-win32.whl` |

其余候选wheel包含架构通用标签；其中pythonnet 3.2.1标注支持CPython 3.13，但.NET桥与位数匹配仍需实际加载检查。已读取现有pywebview 6.2.1包内`win-x86/native/WebView2Loader.dll`，PE机器类型为`0x14c`（i386），不是将x64加载器重命名。没有据此宣称WebView2窗口已能在x86运行。[PyPI版本API示例](https://pypi.org/pypi/pywebview/6.2.1/json)、[pythonnet版本API](https://pypi.org/pypi/pythonnet/3.2.1/json)。

Python官方仍提供当前项目所用3.13.3的32位安装包和嵌入式包；这只是解释器来源证据。本轮不改变既有Python版本，也不将PyInstaller自身最低OS声明当作整个产品的最低OS承诺。[Python 3.13.3发布文件](https://www.python.org/downloads/release/python-3133/)、[PyInstaller要求](https://pyinstaller.org/en/stable/requirements.html)。

## 固定WebView2 x86

微软官网下载元数据提供与x64当前版本相同的`Microsoft.WebView2.FixedVersionRuntime.157.0.4341.42.x86.cab`。本次只做HTTP HEAD验证，返回200，大小269,637,143字节（约257.15 MiB）；未下载完整CAB，未计算其SHA256，也未加入运行时锁文件。不能套用x64 CAB的校验值。

候选来自[微软WebView2官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)，具体文件标识为`67a684f9-0ad9-4651-8687-570e472630f8`。若后续实施，需独立固定该CAB的实际哈希、完整资源和许可，确认x86加载器、浏览器、Python与.NET一致，再做离线原生窗口测试。[微软固定运行时分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

## WMF：主要额外工作

MSYS2将MINGW32列为legacy、i686/MSVCRT环境；32位环境从2023年开始逐步退出，CLANG32已于2024年移除。现有x64方案是UCRT64，不能只替换目录名就视为等价移植。[MSYS2环境及变更记录](https://www.msys2.org/docs/environments/)。

历史包索引记录`mingw-w64-i686-libwmf 0.2.15-1`，早于当前x64的0.2.16-1。其包页面在本次直接访问返回404；搜索服务仍有历史页面。因此另对官方主服务器具体文件做HEAD检查：

| 文件 | 本次直接HTTP结果 | 大小 |
|---|---|---:|
| [i686 libwmf二进制包](https://repo.msys2.org/mingw/mingw32/mingw-w64-i686-libwmf-0.2.15-1-any.pkg.tar.zst) | 200 | 1,991,344字节 |
| [对应源码归档](https://repo.msys2.org/mingw/sources/mingw-w64-libwmf-0.2.15-1.src.tar.zst) | 200 | 2,633,358字节 |

文件仍存在并不证明依赖闭包完整或持续维护。本轮没有下载它们、复核实际SHA、枚举i686 PE依赖、核对全部源码/字体许可，也没有运行WMF转换。若继续，需评估固定历史i686依赖与自行构建当前源码两条路径的维护成本；不能混入现有x64 DLL，也不能降低字体/公式审计门槛。可选LibreOffice原图引擎依然未验证，不作x86覆盖承诺。

## 验收边界

后续是否实施，应先确认需求是“x86程序运行在64位Windows”还是“32位Windows操作系统”，再明确目标OS。x64云端runner上运行32位程序仅能证明该环境中的WOW64路径，不能替代真正32位OS实测。仍需独立的依赖锁、PE位数/完整性检查、冻结CLI及原生窗口冒烟，以及中文路径、离线、可写用户数据目录和代表题语义比较。

当前x64 CI的artifact存储额度问题是账户分发通道限制，与x86依赖是否可用无关；不应为绕过它而启动x86构建。本次没有创建x86工作流或发布包。
