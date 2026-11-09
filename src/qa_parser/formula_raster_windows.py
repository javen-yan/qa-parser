"""Pinned WebView2 capture host; classification and exported semantics are shared."""
import io
import json
import hashlib
import math
import platform
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

from .formula_raster import offline_page, validate_png

ENGINE = 'webview2-katex-raster/1'


def crop_capture(data, rect):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        sx,sy=im.width/rect['viewportWidth'],im.height/rect['viewportHeight']
        if abs(sx-sy)>0.05 or rect['x']<0 or rect['y']<0:
            raise ValueError('WebView capture geometry differs from CSS viewport')
        box=(math.floor(rect['x']*sx), math.floor(rect['y']*sy),
             math.ceil((rect['x']+rect['width'])*sx), math.ceil((rect['y']+rect['height'])*sy))
        if box[2]>im.width or box[3]>im.height:
            raise ValueError('Formula capture would be cropped')
        stream=io.BytesIO();im.crop(box).save(stream,format='PNG')
        return stream.getvalue()


def native_main(input_path, output_path):
    import sys
    if getattr(sys, 'frozen', False):
        from .startup_diagnostics import auto_recover
        from .desktop import user_data_directory
        auto_recover(Path(sys.executable).parent, user_data_directory() / 'diagnostics/startup-recovery.json')
    import webview
    from .desktop_window import configure_windows_runtime
    configure_windows_runtime(webview)
    formulas=json.loads(Path(input_path).read_text(encoding='utf-8'))
    if not isinstance(formulas,list) or len(formulas)>4096 or any(not isinstance(s,str) or len(s)>65536 for s in formulas):
        raise ValueError('Invalid formula batch')
    output_path=Path(output_path)
    page,fingerprint=offline_page();payload=page.encode('utf-8')
    # NavigateToString has a small size limit; the bundled CJK font is larger.
    # Serve this one immutable inline page only, never a directory or input API.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/':self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    class Server(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name='127.0.0.1';self.server_port=self.server_address[1]
    server=Server(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    window=webview.create_window('Formula renderer',f'http://127.0.0.1:{server.server_port}/',
                                 width=2000,height=500,hidden=True,focus=False)
    done=threading.Event()
    def loaded():
        from System import Func,Object,Action
        from System.IO import MemoryStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        rows=[]
        try:
            control=window.native.webview
            # Keep the rendering control visible without showing a taskbar
            # window or taking focus. All native operations run on its UI thread.
            def show_invisible():
                window.native.Opacity=0
                window.native.ShowInTaskbar=False
                window.native.Show()
            control.Invoke(Action(show_invisible))
            for index,tex in enumerate(formulas):
                row={'latex_sha256':hashlib.sha256(tex.encode()).hexdigest(),'engine':ENGINE,
                     'renderer_resources_sha256':fingerprint,'platform':platform.platform()}
                try:
                    window.run_js('window.formulaReady=false;window.formulaError=null;try{QaMathPreview.render('+json.dumps(tex)+',document.getElementById("formula"));void document.getElementById("formula").offsetWidth;document.fonts.ready.then(()=>window.formulaReady=true)}catch(e){window.formulaError=String(e)};void 0')
                    deadline=time.monotonic()+15
                    while window.run_js('window.formulaReady===true') is not True:
                        error=window.run_js('window.formulaError')
                        if error:raise ValueError(error)
                        if time.monotonic()>deadline:raise TimeoutError('Formula fonts did not load')
                        time.sleep(.02)
                    rect=json.loads(window.run_js('JSON.stringify((()=>{let e=document.getElementById("formula"),r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight,overflow:e.scrollWidth>e.clientWidth}})())'))
                    if rect['overflow'] or rect['width']>rect['viewportWidth'] or rect['height']>rect['viewportHeight']:
                        raise ValueError('Formula exceeds native viewport')
                    stream=MemoryStream()
                    try:
                        capture=control.Invoke(Func[Object](lambda:control.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream)))
                        deadline=time.monotonic()+15
                        while not capture.IsCompleted:
                            if time.monotonic()>deadline:raise TimeoutError('WebView capture timeout')
                            time.sleep(.02)
                        if capture.IsFaulted or capture.IsCanceled:raise ValueError(str(capture.Exception))
                        data=crop_capture(bytes(stream.ToArray()),rect)
                    finally:
                        stream.Dispose()
                    dimensions=validate_png(data)
                    (output_path.parent/f'{index}.png').write_bytes(data)
                    row.update(status='converted',png_sha256=hashlib.sha256(data).hexdigest(),**dimensions)
                except Exception as exc:
                    row.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
                rows.append(row)
            output_path.write_text(json.dumps(rows,ensure_ascii=False,indent=2), encoding='utf-8')
            done.set()
        finally:
            window.destroy()
    window.events.loaded += loaded
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    webview.settings['ALLOW_FILE_URLS']=False
    try:
        webview.start(gui='edgechromium',private_mode=True,debug=False,storage_path=str(output_path.parent/'webview-cache'))
    finally:
        server.shutdown();server.server_close()
    return 0 if done.is_set() else 1
