"""Loopback-only, offline UI host. Static allowlist; no generic file server."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import hashlib
import logging
import os
import secrets
import shutil
import threading
import urllib.parse
import uuid
import webbrowser

from .desktop import open_local_path, user_data_directory

MAX_UPLOAD = 128 * 1024 * 1024
MAX_FILES = 100
WEB = Path(__file__).with_name("web")


class UIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Application:
    def __init__(self, data_directory: Path, output_directory: Path):
        self.data_directory = data_directory.resolve()
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.uploads = self.data_directory / ("session-" + uuid.uuid4().hex)
        self.uploads.mkdir()
        self.output_directory = output_directory.expanduser().resolve()
        self.lock = threading.RLock()
        self.busy = False
        self.choosing = False
        self.files = []
        self.batch = None
        self.worker = None
        self.token = secrets.token_urlsafe(32)
        self.logger = logging.getLogger("qa-parser-ui-" + uuid.uuid4().hex)
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler(self.data_directory / "interface.log", encoding="utf-8")
        self.logger.addHandler(handler)

    def state(self):
        with self.lock:
            batch = deepcopy(self.batch)
            if batch:
                batch["total"] = batch.get("total_files", len(batch.get("files", [])))
                for row in batch.get("files", []):
                    row["status"] = {"running": "processing", "queued": "pending"}.get(row["status"], row["status"])
                    row["exported"] = row.get("passed", 0)
                    row["omitted"] = row.get("quarantined", 0)
            return {"busy": self.busy, "output_directory": str(self.output_directory),
                    "files": [{k: f[k] for k in ("id", "name", "size")} for f in self.files], "batch": batch}

    def require_idle(self):
        if self.busy or self.choosing:
            raise UIError("正在处理，请等待当前操作完成。", 409)

    def add_file(self, name, stream, size):
        # Browser-supplied names are labels, never trusted paths.
        name = name.replace("\\", "/").split("/")[-1]
        if not name.lower().endswith(".docx") or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise UIError("请添加 Word 文档（.docx）。")
        if size <= 0 or size > MAX_UPLOAD:
            raise UIError("文件为空或超过 128 MB，未添加。", 413)
        with self.lock:
            self.require_idle()
            if self.batch:
                raise UIError("本批次已完成，请先点击新建批次。", 409)
            if len(self.files) >= MAX_FILES:
                raise UIError("每批最多添加 100 份文档。")
            file_id = uuid.uuid4().hex
            folder = self.uploads / file_id
            folder.mkdir()
            path = folder / name
            try:
                with path.open("xb") as output:
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise UIError("文件添加中断，请重新添加。")
                        output.write(chunk)
                        remaining -= len(chunk)
            except Exception:
                shutil.rmtree(folder)
                raise
            self.files.append({"id": file_id, "name": name, "size": size, "path": path})
        return self.state()

    def remove(self, file_id):
        with self.lock:
            self.require_idle()
            if self.batch:
                raise UIError("请先新建批次。", 409)
            selected = next((f for f in self.files if f["id"] == file_id), None)
            if not selected:
                raise UIError("文件已移除。", 404)
            shutil.rmtree(selected["path"].parent)
            self.files.remove(selected)
        return self.state()

    def _folder(self, value=None):
        home = Path.home().resolve()
        path = Path(value).expanduser().resolve() if value else (self.output_directory if self.output_directory.is_dir() else home)
        if not path.is_relative_to(home):
            raise UIError("请选择个人文件夹内的保存位置。")
        if not path.is_dir():
            raise UIError("此文件夹不存在或无法读取。")
        return path

    def folders(self, value=None):
        with self.lock:
            path = self._folder(value)
        home = Path.home().resolve()
        try:
            folders = []
            for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
                if not child.name.startswith(".") and child.is_dir() and child.resolve().is_relative_to(home):
                    folders.append({"name": child.name, "path": str(child.resolve())})
            shortcuts = [{"name": name, "path": str(folder)} for name, folder in [
                ("个人文件夹", home), ("文稿", home / "Documents"),
                ("桌面", home / "Desktop"), ("下载", home / "Downloads")]
                if folder.is_dir()]
            return {"path": str(path), "parent": str(path.parent) if path != home else None,
                    "folders": folders, "shortcuts": shortcuts}
        except OSError as exc:
            raise UIError("无法读取此文件夹，请选择其他位置。") from exc

    def choose_output(self, value):
        with self.lock:
            self.require_idle()
            if self.batch:
                raise UIError("请先新建转换，再更改保存位置。", 409)
            self.output_directory = self._folder(value)
        return self.state()

    def start(self, mode):
        if mode not in {"auto", "choice", "blank"}:
            raise UIError("请选择转换方式。")
        with self.lock:
            self.require_idle()
            if self.batch:
                raise UIError("本批次已经处理，请先新建批次。", 409)
            if not self.files:
                raise UIError("请先添加 Word 文档。")
            try:
                self.output_directory.mkdir(parents=True, exist_ok=True)
                probe = self.output_directory / (".qa-write-" + uuid.uuid4().hex)
                probe.write_bytes(b"")
                probe.unlink()
            except OSError as exc:
                raise UIError("无法保存到此文件夹，请选择其他输出位置。") from exc
            name = "题库转换-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            out = self.data_directory / "jobs" / name
            delivery = self.output_directory / name
            self.busy = True
            self.batch = {"status": "running", "message": "正在准备转换…", "completed": 0,
                          "total_files": len(self.files), "files": [], "output": str(out)}
            inputs = [f["path"] for f in self.files]
            self.worker = threading.Thread(target=self._run, args=(inputs, mode, out, delivery), daemon=False)
            self.worker.start()
        return self.state()

    def _progress(self, snapshot):
        with self.lock:
            self.batch = deepcopy(snapshot)

    def _run(self, inputs, mode, out, delivery):
        try:
            from .batch import run_batch
            result = run_batch(inputs, mode, out, progress=self._progress, delivery_directory=delivery)
            with self.lock:
                self.batch = result
        except Exception:
            self.logger.exception("Batch could not finish")
            message = "处理未能完成。已生成的文件保留在输出目录，请查看结果说明。"
            report = None
            try:
                out.mkdir(parents=True, exist_ok=True)
                report = out / "interface-failure.txt"
                report.write_text(message + "\n诊断位置：" + str(self.data_directory / "interface.log"), encoding="utf-8")
            except OSError:
                pass
            with self.lock:
                self.batch.update(status="failure", message=message, report=str(report) if report else None)
        finally:
            with self.lock:
                self.busy = False

    def reset(self):
        with self.lock:
            self.require_idle()
            for f in self.files:
                shutil.rmtree(f["path"].parent, ignore_errors=True)
            self.files.clear()
            self.batch = None
        return self.state()

    def open_result(self, target, index=None):
        with self.lock:
            if not self.batch:
                raise UIError("尚未生成结果。")
            if target == "batch":
                path = self.batch.get("delivery_directory")
            elif target == "diagnostics":
                path = self.batch.get("output")
            else:
                rows = self.batch.get("files", [])
                if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(rows):
                    raise UIError("未找到此文件的结果。")
                row = rows[index]
                if row["status"] not in {"success", "partial_failure", "failure"}:
                    raise UIError("此文件仍在处理。")
                if target == "source":
                    path = row.get("source_snapshot")
                    if path and (not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != row.get("source_sha256")):
                        raise UIError("源 Word 快照缺失或发生变化，暂时不能打开。")
                else:
                    path = row.get("export") if target == "output" else row.get("report") if target == "report" else None
            if not path:
                raise UIError("此文件尚无可用结果。")
            path = Path(path)
        open_local_path(path)
        return self.state()

    def read_problems(self, index):
        with self.lock:
            rows = (self.batch or {}).get("files", [])
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(rows):
                raise UIError("未找到此文件的题目说明。", 404)
            row = deepcopy(rows[index])
        if row.get("status") not in {"success", "partial_failure", "failure"}:
            raise UIError("此文件尚在处理，请完成后查看。", 409)
        source = row.get("source_snapshot")
        available = bool(source and Path(source).is_file())
        if available:
            available = hashlib.sha256(Path(source).read_bytes()).hexdigest() == row.get("source_sha256")
        task = Path(row["output"]) / "task"
        if (task / "task.json").is_file():
            from .problem_preview import build_problem_preview
            preview = build_problem_preview(task, row["name"])
        else:
            preview = {"schema_version": 1, "source_name": row["name"], "questions": [],
                       "empty_message": row.get("message", "此文件没有可读取的题目结构，无法提供逐题预览。"),
                       "document_issues": [{"category": "document", "label": "无法生成题目预览",
                           "reason": row.get("message", "无法读取题目内容"),
                           "impact": "未取得可靠题目结构，不能确认题目内容或失败位置。可使用系统默认程序检查源 Word。"}]}
        preview["source_available"] = available
        return preview

    def read_report(self, index):
        with self.lock:
            rows = (self.batch or {}).get("files", [])
            if not isinstance(index, int) or index < 0 or index >= len(rows):
                raise UIError("未找到此文件的结果说明。", 404)
            row = rows[index]
            if not row.get("report"):
                raise UIError("结果说明尚未生成。", 404)
            path = Path(row["report"])
            if not path.is_file():
                raise UIError("结果说明已移动或删除。", 404)
            return {"name": row["name"], "text": path.read_text(encoding="utf-8")}

    def read_export_preview(self, index):
        import base64
        from .export_preview import build_export_preview
        with self.lock:
            rows = (self.batch or {}).get('files', [])
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows):
                raise UIError('未找到此文件的题目。', 404)
            row = deepcopy(rows[index])
        if not row.get('export') or row.get('status') not in {'success', 'partial_failure'}:
            raise UIError('此文件没有可预览的导出题目。', 404)
        root = Path(row['export'])
        task = json.loads((Path(row['output']) / 'task/task.json').read_text(encoding='utf-8'))
        config = task['config']
        preview = build_export_preview(root, row['name'], formula_delimiter=config['formula_delimiter'],
                                       image_reference=config['image_reference'])
        cache = {}
        for question in preview['questions']:
            question['kind'] = preview['kind']
            for field in question['fields']:
                for segment in field['segments']:
                    if segment['type'] == 'image':
                        name = segment['filename']
                        if name not in cache:
                            from PIL import Image
                            import io
                            with Image.open(root / 'images' / name) as im:
                                buf = io.BytesIO()
                                im.save(buf, format='PNG')
                            cache[name] = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
                        segment['src'] = cache[name]
        preview['exported'] = True
        preview['source_available'] = False
        return preview


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, app: Application, port=0):
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _check(self, api=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            raise UIError("无法访问此本地服务。", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise UIError("不允许从其他页面发起操作。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise UIError("不允许从其他页面发起操作。", 403)
        if api and not secrets.compare_digest(self.headers.get("X-QA-Token", ""), self.server.app.token):
            raise UIError("页面已失效，请重新打开转换工具。", 403)

    def do_GET(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            self._check(path.startswith("/api/"))
            if path == "/api/state":
                return self._send(200, self.server.app.state())
            if path == "/api/folders":
                value = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("path", [None])[0]
                return self._send(200, self.server.app.folders(value))
            if path == "/api/problems":
                try:
                    index = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["index"][0])
                except (ValueError, KeyError, IndexError):
                    raise UIError("未找到此文件的题目说明。", 404)
                return self._send(200, self.server.app.read_problems(index))
            if path == "/api/export-preview":
                try:
                    index = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["index"][0])
                except (ValueError, KeyError, IndexError):
                    raise UIError("未找到此文件的题目。", 404)
                return self._send(200, self.server.app.read_export_preview(index))
            if path == "/api/report":
                try:
                    index = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["index"][0])
                except (ValueError, KeyError, IndexError):
                    raise UIError("未找到此文件的结果说明。", 404)
                return self._send(200, self.server.app.read_report(index))
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/index.html": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/math-preview.js": ("math-preview.js", "text/javascript; charset=utf-8"),
                      "/font-coverage.js": ("font-coverage.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            assets.update({"/vendor/katex/katex.min.js": ("vendor/katex/katex.min.js", "text/javascript; charset=utf-8"),
                           "/vendor/katex/katex.min.css": ("vendor/katex/katex.min.css", "text/css; charset=utf-8")})
            for font in (WEB / "vendor/katex/fonts").glob("*.woff2"):
                assets["/vendor/katex/fonts/" + font.name] = ("vendor/katex/fonts/" + font.name, "font/woff2")
            assets["/vendor/fonts/display-font.css"] = ("vendor/fonts/display-font.css", "text/css; charset=utf-8")
            assets["/vendor/fonts/NotoSansCJKsc-Regular.otf"] = ("vendor/fonts/NotoSansCJKsc-Regular.otf", "font/otf")
            if path not in assets:
                raise UIError("页面不存在。", 404)
            name, mime = assets[path]
            content = (WEB / name).read_bytes()
            if name == "index.html":
                content = content.replace(b"__QA_TOKEN__", self.server.app.token.encode())
            self._send(200, content, mime)
        except UIError as exc:
            self._send(exc.status, {"error": str(exc)})
        except Exception:
            self.server.app.logger.exception("GET failed")
            self._send(500, {"error": "页面暂时无法打开，请重新启动工具。"})

    def do_POST(self):
        try:
            self._check(api=True)
            if self.headers.get("Transfer-Encoding"):
                raise UIError("文件传输方式不支持。")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise UIError("请求内容不完整。")
            app = self.server.app
            if self.path == "/api/files":
                name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
                return self._send(200, app.add_file(name, self.rfile, size))
            if not 0 <= size <= 8192:
                raise UIError("请求内容过大。", 413)
            try:
                args = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(args, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise UIError("请求内容不完整。")
            if self.path == "/api/remove":
                state = app.remove(args.get("id"))
            elif self.path == "/api/output":
                state = app.choose_output(args.get("path"))
            elif self.path == "/api/start":
                state = app.start(args.get("mode"))
            elif self.path == "/api/reset":
                state = app.reset()
            elif self.path == "/api/open":
                state = app.open_result(args.get("target"), args.get("index"))
            elif self.path == "/api/shutdown":
                with app.lock:
                    app.require_idle()
                    state = app.state()
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise UIError("操作不存在。", 404)
            self._send(200, state)
        except UIError as exc:
            self._send(exc.status, {"error": str(exc)})
        except Exception:
            self.server.app.logger.exception("UI operation failed")
            self._send(500, {"error": "操作未完成，请重试或选择其他保存位置。"})


def serve(data_directory=None, output_directory=None, port=0, open_browser=True):
    app = Application(Path(data_directory) if data_directory else user_data_directory(),
                      Path(output_directory) if output_directory else Path.home() / "Documents" / "题库转换结果")
    server = LocalServer(app, port)
    instance_file = app.data_directory / "server.json"
    instance_temp = instance_file.with_suffix(".tmp")
    instance_temp.write_text(json.dumps({"url": server.origin, "pid": os.getpid()}), encoding="utf-8")
    instance_temp.replace(instance_file)
    print(f"题库转换界面：{server.origin}", flush=True)
    if open_browser:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if app.worker:
            app.worker.join()
        shutil.rmtree(app.uploads, ignore_errors=True)
        try:
            if json.loads(instance_file.read_text())["pid"] == os.getpid():
                instance_file.unlink()
        except (OSError, ValueError, KeyError):
            pass
    return 0
