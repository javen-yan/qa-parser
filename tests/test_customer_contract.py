import json
from copy import deepcopy
import pytest
from qa_parser.config import DEFAULT_CONFIG
from qa_parser.core import _prepare_blocks, _render_questions
from qa_parser.exporter import export_task, ExportError
from qa_parser.export_preview import build_export_preview
from qa_parser.image_references import referenced_names
from test_export_preview import exported
from test_exporter import sample_task, cells


def test_display_alias_maps_to_physical_file_and_preview_validates_mapping(tmp_path):
    folder, report=exported(tmp_path,image=True)
    assert report['image_reference_map']=={'参考aaa001.png)':'001.png'}
    assert (folder/'images/001.png').is_file() and not (folder/'images/aaa001.png').exists()
    preview=build_export_preview(folder)
    images=[s for q in preview['questions'] for f in q['fields'] for s in f['segments'] if s['type']=='image']
    assert images[0]['src']=='images/001.png'
    report['image_reference_map']={'参考aaa001.png)':'aaa001.png'}
    (folder/'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='mapping'):build_export_preview(folder)


def test_alias_decoding_never_selects_a_substring_resource(tmp_path):
    task=sample_task();task['questions'][0]['stem']='参考aaa1001.png)'
    task['assets']=[{'filename':'001.png','path':'001.png','sha256':'unused'}]
    with pytest.raises(ExportError,match='1001.png'):export_task(task,tmp_path,tmp_path/'out')
    assert referenced_names('前参考aaa001.png)后参考aaa1001.png)',DEFAULT_CONFIG['image_reference'])=={'001.png','1001.png'}


def test_numbering_grows_without_truncation(tmp_path):
    document={'blocks':[{'index':0,'tokens':[{'kind':'image','data':b'original','extension':'png'} for _ in range(1001)]}]}
    _,_,assets=_prepare_blocks(document,tmp_path,DEFAULT_CONFIG)
    assert [assets[i]['filename'] for i in [0,998,999,1000]]==['001.png','999.png','1000.png','1001.png']
    assert len({a['filename'] for a in assets})==1001


@pytest.mark.parametrize('old,new',[('低','易'),('中','中'),('高','难'),('易','易'),('难','难')])
def test_legacy_and_manual_difficulty_keep_rank_on_export(tmp_path,old,new):
    task=sample_task();task['questions'][0]['difficulty']=old
    export_task(task,tmp_path,tmp_path/'out')
    assert cells(tmp_path/'out/questions.xlsx')[1]['C2']==new


def test_difficulty_selection_remains_stable_across_label_change():
    new=deepcopy(DEFAULT_CONFIG);old={**new,'difficulty_levels':['低','中','高']}
    for i in range(50):
        q={'raw_text':f'不同题目{i}','stem':str(i),'answer':'A','analysis':'','options':{}}
        left,right=deepcopy(q),deepcopy(q)
        _render_questions([left],[],[],old);_render_questions([right],[],[],new)
        assert right['difficulty']=={'低':'易','中':'中','高':'难'}[left['difficulty']]
