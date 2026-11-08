"""Offline native rerendering of structurally accepted TeX; never an original preview.

Uses native WKWebView or the packaged WebView2 host. Other platforms fail explicitly;
no PATH browser, network, Python/Node installer or developer renderer fallback.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ENGINE = 'wkwebview-katex-raster/1'


def validate_png(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if im.format != 'PNG' or im.width > 4096 or im.height > 1024:
            raise ValueError('Invalid raster format or dimensions')
        im.load()
        if 'A' in im.getbands() and im.getchannel('A').getextrema() != (255, 255):
            raise ValueError('Formula capture has unverified transparency')
        gray = im.convert('RGB').convert('L')
        box = gray.point(lambda n: 255 if n < 160 else 0).getbbox()
        if not box or min(box[0], box[1], im.width-box[2], im.height-box[3]) < 8:
            raise ValueError('Formula raster is empty or reaches the crop boundary')
        return {'width': im.width, 'height': im.height, 'ink_bbox': list(box)}


def render_formulas(formulas):
    formulas = list(dict.fromkeys(formulas))
    failed = lambda reason: {tex: {'status': 'failed', 'reason': reason, 'engine': ENGINE} for tex in formulas}
    if not formulas:
        return {}
    if sys.platform not in {'darwin', 'win32'}:
        return failed('Native formula PNG rendering has not been validated on this platform')
    if len(formulas) > 4096 or any(len(tex) > 65536 for tex in formulas):
        return failed('Formula raster batch exceeds bounded limits')
    try:
        with tempfile.TemporaryDirectory(prefix='qa-formula-raster-') as folder:
            root = Path(folder)
            (root/'input.json').write_text(json.dumps(formulas), encoding='utf-8')
            base = [sys.executable, '--cli'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'qa_parser']
            process = subprocess.Popen(base + ['_rasterize-formulas', str(root/'input.json'), str(root/'report.json')],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
            try:
                process.communicate(timeout=min(300, 30 + 2 * len(formulas)))
            except subprocess.TimeoutExpired:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=10, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.kill()
                process.communicate(timeout=10)
                return failed('Native formula renderer timed out; source evidence retained')
            if process.returncode or not (root/'report.json').is_file():
                return failed('Native formula renderer did not complete; source evidence retained')
            report = json.loads((root/'report.json').read_text(encoding='utf-8'))
            if len(report) != len(formulas):
                return failed('Native formula result count mismatch')
            output = {}
            for index, (tex, row) in enumerate(zip(formulas, report)):
                if row.get('latex_sha256') != hashlib.sha256(tex.encode()).hexdigest():
                    return failed('Native formula identity mismatch')
                if row['status'] == 'converted':
                    data = (root/f'{index}.png').read_bytes()
                    dimensions = validate_png(data)
                    if hashlib.sha256(data).hexdigest() != row['png_sha256']:
                        return failed('Native formula PNG changed')
                    row.update(png=data, **dimensions)
                output[tex] = row
            return output
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return failed(f'Native formula rendering unavailable: {type(exc).__name__}: {exc}')


def offline_page():
    from .problem_preview import _embedded_math
    css, scripts = _embedded_math()
    # Inline-only pinned resources; forbid all navigation/network content.
    page = ('<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; font-src data:; img-src data:; connect-src \'none\'">'
            '<style>'+css+'body{margin:0;background:white}#formula{display:inline-block;padding:16px;font-size:32px;white-space:nowrap}</style><div id="formula"></div>'
            + ''.join('<script>'+s+'</script>' for s in scripts[:3]) + '<script>window.rendererReady=true</script>')
    fingerprint = hashlib.sha256((css + ''.join(scripts[:3])).encode()).hexdigest()
    return page, fingerprint


def native_main(input_path, output_path):
    """One hidden native view per document; all AppKit work stays on main thread."""
    if sys.platform == 'win32':
        from .formula_raster_windows import native_main as windows_main
        return windows_main(input_path, output_path)
    if sys.platform != 'darwin':
        raise RuntimeError('Native formula raster adapter is not validated on this platform')
    import time
    import platform
    import AppKit
    import Foundation
    import WebKit
    from .problem_preview import _embedded_math

    formulas = json.loads(Path(input_path).read_text(encoding='utf-8'))
    if not isinstance(formulas, list) or len(formulas) > 4096 or any(not isinstance(s,str) or len(s)>65536 for s in formulas):
        raise ValueError('Invalid formula batch')
    output_path = Path(output_path)
    AppKit.NSApplication.sharedApplication()
    view = WebKit.WKWebView.alloc().initWithFrame_configuration_(AppKit.NSMakeRect(0,0,2000,500), WebKit.WKWebViewConfiguration.alloc().init())
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(AppKit.NSMakeRect(0,0,2000,500),0,AppKit.NSBackingStoreBuffered,False)
    window.setContentView_(view)
    page, fingerprint = offline_page()
    view.loadHTMLString_baseURL_(page,None)
    def tick():
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(.02))
    def wait(items):
        deadline = time.monotonic()+15
        while not items and time.monotonic()<deadline: tick()
        if not items: raise TimeoutError('Native WebKit callback timed out')
    def js(code):
        result=[]
        view.evaluateJavaScript_completionHandler_(code, lambda value,error: result.append((value,error)))
        wait(result)
        if result[0][1]: raise ValueError(str(result[0][1]))
        return result[0][0]
    deadline = time.monotonic()+20
    while not js('window.rendererReady===true'):
        if time.monotonic()>deadline: raise TimeoutError('Offline formula page did not load')
        tick()
    rows=[]
    for index,tex in enumerate(formulas):
        row={'latex_sha256':hashlib.sha256(tex.encode()).hexdigest(), 'engine':ENGINE,
             'renderer_resources_sha256':fingerprint, 'platform':platform.platform()}
        try:
            js('window.formulaReady=false;window.formulaError=null;try{QaMathPreview.render('+json.dumps(tex)+',document.getElementById("formula"));void document.getElementById("formula").offsetWidth;document.fonts.ready.then(()=>{window.formulaReady=true})}catch(e){window.formulaError=String(e)};void 0')
            deadline=time.monotonic()+15
            while not js('window.formulaReady===true'):
                error=js('window.formulaError')
                if error: raise ValueError(error)
                if time.monotonic()>deadline: raise TimeoutError('Formula fonts did not finish loading')
                tick()
            rect=json.loads(js('JSON.stringify((()=>{let e=document.getElementById("formula"),r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,overflow:e.scrollWidth>e.clientWidth}})())'))
            if rect['overflow'] or rect['width']>2000 or rect['height']>500:
                raise ValueError('Formula exceeds native viewport; refusing cropped image')
            config=WebKit.WKSnapshotConfiguration.alloc().init()
            config.setRect_(AppKit.NSMakeRect(rect['x'],rect['y'],rect['width'],rect['height']))
            # WKSnapshotWidth is in points: Retina otherwise doubles our 2x
            # request again and rejects valid long formulas at the pixel cap.
            config.setSnapshotWidth_(rect['width']*2/window.backingScaleFactor())
            captured=[]
            view.takeSnapshotWithConfiguration_completionHandler_(config,lambda image,error:captured.append((image,error)))
            wait(captured)
            image,error=captured[0]
            if error or image is None: raise ValueError(str(error))
            rep=AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
            data=bytes(rep.representationUsingType_properties_(AppKit.NSPNGFileType,{}))
            dimensions=validate_png(data)
            (output_path.parent/f'{index}.png').write_bytes(data)
            row.update(status='converted',png_sha256=hashlib.sha256(data).hexdigest(),**dimensions)
        except Exception as exc:
            row.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    output_path.write_text(json.dumps(rows,ensure_ascii=False,indent=2), encoding='utf-8')
    window.orderOut_(None)
    return 0
