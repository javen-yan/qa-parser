import io
import json
from pathlib import Path
from copy import deepcopy
from zipfile import ZipFile

from PIL import Image, ImageDraw
import pytest

from qa_parser.config import digest
from qa_parser.formula_policy import decide, apply_field_context
from qa_parser.core import parse_document, load_task, validate_task, export_document
from test_core import p, write_docx


@pytest.mark.parametrize('latex', ['U=IR', 'R_x', 'S_{1}', r'R=\frac{U}{I}', r'x^2+y^2', r'\sqrt{x}'])
def test_simple_scientific_text_is_kept(latex):
    assert decide(latex)['mode'] == 'text'


@pytest.mark.parametrize('latex,reason', [
    ('I_1R_x=I_2R_0', 'multiple_subscripts_emphasis_risk'),
    (r'R_x=\frac{I_2R_0}{I_1}', 'multiple_subscripts_emphasis_risk'),
    ('x_1^2', 'combined_subscript_superscript'),
    (r'\frac{a}{\frac{b}{c}}', 'structure_outside_conservative_text_subset'),
    (r'\xrightarrow{加热}', 'structure_outside_conservative_text_subset'),
    ('I<em>2R</em>0', 'formula_contains_markup_or_delimiter'),
])
def test_risks_have_explainable_decisions(latex, reason):
    assert decide(latex)['reason'] == reason
    assert decide(latex)['mode'] != 'text'


def test_chinese_between_simple_subscripts_cannot_hide_field_pairing_risk():
    formulas = {'F1': {'latex':'R_x'}, 'F2':{'latex':'R_0'}}
    policies = {key:decide(f['latex']) for key,f in formulas.items()}
    apply_field_context(['F1','F2'],formulas,policies)
    assert all(p['reason']=='multiple_subscript_formulas_in_same_field' for p in policies.values())


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


def test_field_context_mixed_export_and_reload_binding(tmp_path,monkeypatch):
    install_adapter(monkeypatch)
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas',fake_native)
    report=parse_document(source(tmp_path),'choice',tmp_path/'task')
    assert report['passed']==1
    task,root=load_task(tmp_path/'task')
    q=task['questions'][0]
    assert r'$$R=\frac{U}{I}$$' in q['analysis']
    assert '参考aaa0001.png)与参考aaa0002.png)' in q['analysis']
    assert [f['status'] for f in task['formulas']]==['converted','image','image']
    assert len({a['filename'] for a in task['assets']})==2
    assert validate_task(task,root)['passed']==1
    exported=export_document(root,tmp_path/'export')
    assert len(exported['images'])==2
    changed=deepcopy(task);changed['formulas'][1]['latex']='R_z'
    with pytest.raises(ValueError, match='metadata changed'):
        validate_task(changed,root)
    from qa_parser.core import _metadata_digest
    changed['metadata_sha256'] = _metadata_digest(changed)
    assert validate_task(changed,root)['passed']==0
    changed=deepcopy(task);changed['formulas'][1]['raster_evidence']['renderer_resources_sha256']='b'*64
    with pytest.raises(ValueError, match='metadata changed'):
        validate_task(changed,root)
    from qa_parser.core import _metadata_digest
    changed['metadata_sha256'] = _metadata_digest(changed)
    assert validate_task(changed,root)['passed']==0


def test_unavailable_native_renderer_does_not_silently_keep_risky_text(tmp_path,monkeypatch):
    install_adapter(monkeypatch)
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas',lambda xs:{x:{'status':'failed','reason':'platform unavailable'} for x in xs})
    report=parse_document(source(tmp_path),'choice',tmp_path/'task')
    assert report['passed']==0
    assert any('platform unavailable' in i['message'] for i in report['questions'][0]['issues'])
    assert any(i['code']=='formula_review' for i in report['questions'][0]['issues'])


def test_unvalidated_platform_is_explicit(monkeypatch):
    import qa_parser.formula_raster as raster
    monkeypatch.setattr(raster.sys,'platform','linux')
    assert raster.render_formulas(['x_1+x_2'])['x_1+x_2']['status']=='failed'


def test_combined_options_cell_checks_cross_option_subscripts(tmp_path, monkeypatch):
    install_adapter(monkeypatch)
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas', fake_native)
    body=p('1．选择正确答案')+'<w:p><w:r><w:t>A．</w:t></w:r><m:oMath><m:r><m:t>left</m:t></m:r></m:oMath></w:p>'
    body+='<w:p><w:r><w:t>B．</w:t></w:r><m:oMath><m:r><m:t>right</m:t></m:r></m:oMath></w:p>'+p('C．甲 D．乙')+p('【答案】A')
    source=write_docx(tmp_path/'1.docx',body)
    report=parse_document(source,'choice',tmp_path/'task')
    assert report['passed']==1
    task,_=load_task(tmp_path/'task')
    assert all(f['status']=='image' for f in task['formulas'])
    assert all(f['output_policy']['reason']=='multiple_subscript_formulas_in_same_field' for f in task['formulas'])


def test_reliable_original_preview_precedes_rerender_and_keeps_resource_order(tmp_path, monkeypatch):
    from qa_parser.mixed_formulas import apply_mixed_formulas
    preview=b'bound-original';(tmp_path/'preview.wmf').write_bytes(preview)
    f={'id':'F000001','status':'converted','latex':'x_1+x_2','location':'p/1',
       'source':{'path':'source.xml','sha256':'b'*64},
       'preview':{'path':'preview.wmf','sha256':digest(preview)},'preview_binding':{'verified':True}}
    q={'stem':'⟦I000001⟧⟦F000001⟧','answer':'A','analysis':'','options':{}}
    assets=[{'id':'I000001','filename':'0001.jpg','extension':'jpg'}]
    def original(data,ext):
        assert data==preview and ext=='.wmf'
        return fake_native(['x'])['x']
    monkeypatch.setattr('qa_parser.formula_images.preview_to_png',original)
    def native(xs):
        assert not xs, 'Reliable original must not be replaced with reinterpreted TeX'
        return {}
    monkeypatch.setattr('qa_parser.formula_raster.render_formulas',native)
    blocks=[{'tokens':[{'kind':'image','id':'I000001'},{'kind':'formula','id':'F000001'}]}]
    apply_mixed_formulas([q],[f],assets,blocks,tmp_path,{'choice_columns':[]})
    assert [a['filename'] for a in assets]==['aaa0001.jpg','aaa0002.png']
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
