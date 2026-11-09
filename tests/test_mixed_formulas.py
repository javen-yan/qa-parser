import io
from pathlib import Path
from PIL import Image, ImageDraw
import pytest
from qa_parser.config import digest
from qa_parser.formula_policy import decide, apply_field_context
from qa_parser.core import parse_document, load_task, validate_task, export_document
from test_core import p, write_docx

@pytest.mark.parametrize('latex', ['R_1','R_2','R_x','I_1R_x=I_2R_0',r'R_x=\frac{I_2R_0}{I_1}',
    'x_1^2',r'\frac{a}{\frac{b}{c}}',r'\xrightarrow{\text{加热}}',r'\begin{pmatrix}a&b\\c&d\end{pmatrix}'])
def test_reliable_scientific_formulas_stay_text(latex):
    assert decide(latex)['mode']=='text'

@pytest.mark.parametrize('latex', ['x^^2','x__1',r'\frac{a}{b','I<em>2R</em>0',r'\chemfig{C-C}'])
def test_uncertain_syntax_or_diagrams_require_evidence(latex):
    assert decide(latex)['mode']=='review'

def fake_native(formulas):
    # Controlled backend tests resource ownership, not rendering fidelity.
    data=io.BytesIO();im=Image.new('RGB',(80,50),'white');ImageDraw.Draw(im).line((15,25,65,25),fill='black');im.save(data,format='PNG')
    png=data.getvalue()
    return {s:{'status':'converted','png':png,'engine':'test-native','width':80,'height':50,
               'latex_sha256':digest(s.encode()),'png_sha256':digest(png),
               'renderer_resources_sha256':'a'*64} for s in formulas}


def source(tmp_path):
    # Plain Word superscript/subscript groups are also scientific formula inputs.
    math=lambda name:f'<m:oMath><m:r><m:t>{name}</m:t></m:r></m:oMath>'
    body=p('1．选择正确答案')+p('A．甲 B．乙 C．丙 D．丁')+p('【答案】A')
    body+='<w:p><w:r><w:t>【解析】</w:t></w:r>'+math('simple')+'<w:r><w:t>，两端电阻</w:t></w:r>'+math('left')+'<w:r><w:t>与</w:t></w:r>'+math('right')+'</w:p>'
    return write_docx(tmp_path/'01.docx',body)


def install_adapter(monkeypatch):
    def convert(data):
        latex=r'R=\frac{U}{I}' if b'simple' in data else 'R_x' if b'left' in data else 'R_0'
        return {'latex':latex,'status':'converted','reason':'','engine':'test-structure'}
    monkeypatch.setattr('qa_parser.formulas.convert_omml',convert)


def test_same_field_scripts_stay_text_without_native_renderer(tmp_path,monkeypatch):
    install_adapter(monkeypatch)
    def no_raster(xs):
        assert not xs, 'Correct formulas must not need native image rendering'
        return {}
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas',no_raster)
    report=parse_document(source(tmp_path),'choice',tmp_path/'task')
    assert report['passed']==1
    task,root=load_task(tmp_path/'task')
    assert '$$R_x$$与$$R_0$$' in task['questions'][0]['analysis']
    assert all(f['status']=='converted' for f in task['formulas'])
    assert not task['assets'] and validate_task(task,root)['passed']==1
    assert export_document(root,tmp_path/'export')['images']==[]


def test_field_context_never_rasterizes_correct_delimited_formulas():
    formulas={'F1':{'latex':'R_x'},'F2':{'latex':'R_0'}}
    policies={key:decide(value['latex']) for key,value in formulas.items()}
    apply_field_context(['F1','F2'],formulas,policies)
    assert all(p['mode']=='text' for p in policies.values())


def test_unknown_formula_uses_only_verified_original_preview(tmp_path,monkeypatch):
    from qa_parser.mixed_formulas import apply_mixed_formulas
    preview=b'bound-original';(tmp_path/'preview.wmf').write_bytes(preview)
    f={'id':'F000001','status':'converted','latex':'I<em>2</em>','location':'p/1',
       'source':{'path':'source.xml','sha256':'b'*64},
       'preview':{'path':'preview.wmf','sha256':digest(preview)},'preview_binding':{'verified':True}}
    q={'stem':'⟦I000001⟧⟦F000001⟧','answer':'A','analysis':'','options':{}}
    assets=[{'id':'I000001','filename':'001.jpg','extension':'jpg'}]
    monkeypatch.setattr('qa_parser.formula_images.preview_to_png',lambda data,ext:fake_native(['x'])['x'])
    def no_raster(xs):
        assert not xs, 'Unreliable TeX must never be guessed and redrawn'
        return {}
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas',no_raster)
    blocks=[{'tokens':[{'kind':'image','id':'I000001'},{'kind':'formula','id':'F000001'}]}]
    apply_mixed_formulas([q],[f],assets,blocks,tmp_path,{})
    assert [a['filename'] for a in assets]==['001.jpg','002.png']
    assert f['status']=='image' and assets[1]['origin']=='formula_preview'
    assert assets[1]['source_preview_sha256']==digest(preview)


def test_windows_capture_geometry_is_not_assumed_one_pixel_per_css_pixel():
    from qa_parser.formula_raster_windows import crop_capture
    image=Image.new('RGB',(400,200),'white');ImageDraw.Draw(image).rectangle((40,40,80,80),fill='black')
    stream=io.BytesIO();image.save(stream,format='PNG')
    rect={'x':10,'y':10,'width':50,'height':50,'viewportWidth':200,'viewportHeight':100}
    with Image.open(io.BytesIO(crop_capture(stream.getvalue(),rect))) as cropped:
        assert cropped.size==(100,100)
        assert cropped.getpixel((30,30))==(0,0,0)
    with pytest.raises(ValueError,match='cropped'):
        crop_capture(stream.getvalue(),{**rect,'width':300})


def test_invalid_superscript_is_not_mislabeled_simple():
    assert decide('x^^2')['mode'] != 'text'
