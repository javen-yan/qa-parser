# Native helper source inputs

These archives are development/build inputs and are included with the native
helper's license notices in the Mac application. No runtime download occurs.

| File | Upstream source | SHA-256 |
|---|---|---|
| libwmf-0.2.13.tar.gz | https://github.com/caolanm/libwmf/archive/refs/tags/v0.2.13.tar.gz | 18ba69febd2f515d98a2352de284a8051896062ac9728d2ead07bc39ea75a068 |
| libwmf-homebrew.patch | https://github.com/caolanm/libwmf/commit/5c0ffc6320c40a565ff8014d772670df3e0ad87d.patch?full_index=1 | 80ae84a904baa21e1566e3d2bca1c6aaa0a2a30f684fe50f25e7e5751ef3ec93 |
| freetype-2.13.3.tar.xz | https://download.savannah.gnu.org/releases/freetype/freetype-2.13.3.tar.xz | 0550350666d427c74daeb85d5ac7bb353acba5f76956395995311a9c6f063289 |

The installed Homebrew recipe and receipt are copied into the license folder.
Renderer algorithm source is unmodified; install_name_tool changes library
load paths, then codesign applies a local ad-hoc signature. Build script records
original native binary SHA-256 values. Bundled font paths are dynamically
resolved; program folders are never used as writable font caches.

Additional Python bridge notices:

- `pyobjc_core-13.0.tar.gz`: PyPI source distribution, SHA-256
  `ffe154462d14aa4852f424e9116f17b5f8d44f78fc0f6c4727e721a5fa7177da`.
  Its MIT `License.txt` supplements wheel distributions missing that file.
  The built bridge links macOS `/usr/lib/libffi.dylib`; no separate libffi binary is copied.
- `proxy_tools-LICENSE.txt`: https://raw.githubusercontent.com/jtushman/proxy_tools/master/LICENSE.txt
  preserves the upstream BSD text and the 2013/2014 original copyrights. Package
  metadata says MIT while its source header refers to BSD; preserve the full
  upstream notice instead of treating metadata as definitive.
