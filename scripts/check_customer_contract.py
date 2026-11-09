"""Local, read-only comparison of before/after real-reference task outputs."""
import argparse,json,re,hashlib
from collections import Counter
from pathlib import Path
from check_reference import read_xlsx
from qa_parser.export_preview import build_export_preview


def check(baseline,candidate,out):
    old={json.loads(p.read_text())['source']['sha256']:(json.loads(p.read_text()),p.parent) for p in baseline.glob('*/task/task.json')}
    rows=[];latex=[];screens=[]
    for path in sorted(candidate.glob('*/task/task.json')):
        t=json.loads(path.read_text());b,broot=old[t['source']['sha256']];root=path.parent
        count=lambda x:dict(Counter(f['status'] for f in x['formulas']))
        original=lambda x:{(a['location'],a['sha256']) for a in x['assets'] if not a.get('origin')}
        assert original(t)==original(b),t['source']['name']
        for a in t['assets']:
            assert hashlib.sha256((root/a['path']).read_bytes()).hexdigest()==a['sha256']
            assert re.fullmatch(r'\d{3,}\.[a-z0-9]+',a['filename'])
        before={q['number']:q for q in b['questions']}
        for q in t['questions']:
            assert q['difficulty']=={'低':'易','高':'难','中':'中','易':'易','难':'难'}[before[q['number']]['difficulty']]
            if ('酒精' in q['stem'] and '传感器' in q['stem']) or '串联接在6V' in q['stem']:
                screens.append({'source':t['source']['name'],'number':q['number'],'stem':q['stem'],'analysis':q['analysis'],
                    'formulas':[{'id':f['id'],'latex':f.get('latex'),'status':f['status']} for f in t['formulas'] if f['id'] in q['formula_ids']]})
        export=root.parent/'export';manifest=json.loads((export/'report.json').read_text());mapping=manifest['image_reference_map']
        for ref,name in mapping.items():assert ref=='参考aaa'+name+')' and (export/'images'/name).is_file()
        occurrences=0
        for row in read_xlsx(export/'questions.xlsx'):
            for cell in row:
                for match in re.finditer(r'参考aaa\d{3,}\.[a-z0-9]+\)',cell):
                    assert match[0] in mapping
                    occurrences+=1
        assert build_export_preview(export)['counts']['image_placeholders']==0
        for f in t['formulas']:
            if f['status']=='converted':latex.append({'source':t['source']['name'],'id':f['id'],'latex':f['latex']})
        rows.append({'source':t['source']['name'],'questions':len(t['questions']),'exported':manifest['exported_questions'],
                     'before':count(b),'after':count(t),'original_images':len(original(t)),
                     'original_images_sha256_unchanged':True,'difficulty_rank_unchanged':True,'reference_occurrences':occurrences,
                     'exported_images':len(mapping),'omitted':[q['number'] for q in manifest['omitted_questions']]})
    assert len(rows)==9
    out.mkdir(parents=True,exist_ok=True)
    report={'status':'passed','scope':'local nine-reference comparison; not customer APP acceptance','files':rows,'screenshot_matches':screens}
    (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (out/'formula-text.json').write_text(json.dumps(latex,ensure_ascii=False,indent=2))
    print(json.dumps({'files':len(rows),'exported':sum(r['exported'] for r in rows),
                      'before_formula_status':dict(sum((Counter(r['before']) for r in rows),Counter())),
                      'after_formula_status':dict(sum((Counter(r['after']) for r in rows),Counter())),
                      'original_images':sum(r['original_images'] for r in rows)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();check(a.baseline,a.candidate,a.out)
