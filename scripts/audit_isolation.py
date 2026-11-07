"""Read-only source audit of the fixed nine-document quarantine baseline."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import posixpath
import struct
import zipfile
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/bundled-all-nine'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def sha(data): return hashlib.sha256(data).hexdigest()

def wmf_text_records(data):
    offset = (22 if data.startswith(b'\xd7\xcd\xc6\x9a') else 0) + 18
    objects={}; font=None; result=[]
    while offset + 6 <= len(data):
        length, function = struct.unpack_from('<IH', data, offset)
        end=offset+length*2; payload=data[offset+6:end]
        if length<3 or end>len(data): raise ValueError('Invalid WMF record')
        if function==0: break
        if function in (0x02fb,0x02fa,0x02fc):
            handle=next(n for n in range(65536) if n not in objects)
            if function==0x02fb:
                raw=payload[18:].split(b'\0',1)[0]
                fontname=raw.decode('ascii',errors='replace')
                decoded=None
                if any(c>=128 for c in raw):
                    try:decoded=raw.decode('gbk')
                    except UnicodeDecodeError:pass
                objects[handle]={'font_ascii':fontname,'font_raw_hex':raw.hex(),'charset':payload[13],'gbk_decode':decoded}
            else:objects[handle]=None
        elif function==0x012d:
            handle=struct.unpack_from('<H',payload)[0]
            if objects.get(handle) is not None:font=objects[handle]
        elif function==0x01f0:objects.pop(struct.unpack_from('<H',payload)[0],None)
        elif function in (0x0a32,0x0521):
            if function==0x0a32:
                count,options=struct.unpack_from('<HH',payload,4);start=8+(8 if options&6 else 0)
            else:count=struct.unpack_from('<H',payload)[0];start=2
            raw=payload[start:start+count]
            if count:result.append({'font':font,'text_raw_hex':raw.hex(),'ascii_text':raw.decode('ascii') if all(32<=c<=126 for c in raw) else None})
        offset=end
    return result

def field_locations(q,fid):
    fields={'题干':q['stem'],'答案':q['answer'],'解析':q['analysis']}
    fields.update({f'选项{k}':v for k,v in q.get('options',{}).items()})
    return [name for name,value in fields.items() if fid in value]

def main():
    raw_codes=Counter(); reason_counts=Counter(); fallback_counts=Counter(); reason_questions=defaultdict(set); fallback_questions=defaultdict(set)
    documents=[]; questions=[]; formulas=[]; errors=[]; reps={}; checked_sources=0; checked_previews=0; script_records=0
    for quarantine_file in sorted(BASE.glob('*/quarantine.json')):
        folder=quarantine_file.parent; taskroot=folder/'task'; t=json.loads((taskroot/'task.json').read_text()); quarantine=json.loads(quarantine_file.read_text())
        source=(taskroot/t['source']['snapshot']['path']).read_bytes()
        assert sha(source)==t['source']['sha256']
        reference=ROOT/'reference'/t['source']['name']; assert sha(reference.read_bytes())==sha(source)
        qs={q['id']:q for q in t['questions']}; fs={f['id']:f for f in t['formulas']}
        docs={'file':t['source']['name'],'kind':t['kind'],'total':len(qs),'quarantined':len(quarantine['questions']),'source_sha256':sha(source),'document_issues':quarantine['document_issues']}
        documents.append(docs)
        with zipfile.ZipFile(taskroot/t['source']['snapshot']['path']) as z:
            xml=etree.fromstring(z.read('word/document.xml')); namespaces={k:v for k,v in xml.nsmap.items() if k}
            rels={e.get('Id'):e.get('Target') for e in etree.fromstring(z.read('word/_rels/document.xml.rels'))}
            for item in quarantine['questions']:
                q=qs[item['id']]; key=f"{t['source']['name']}#{q['number']}"
                row={'file':t['source']['name'],'question':q['number'],'id':q['id'],'kind':t['kind'],'location':q['location'],'stem_excerpt':q['stem'][:180],'issues':item['issues'],'failed_formulas':[]}
                for issue in item['issues']:raw_codes[issue['code']]+=1
                for fid in dict.fromkeys(i['formula_id'] for i in item['issues'] if i.get('formula_id')):
                    f=fs[fid]; part,xpath=f['location'].split(':',1); nodes=xml.xpath(xpath,namespaces=namespaces); assert len(nodes)==1
                    evidence=(taskroot/f['source']['path']).read_bytes(); assert sha(evidence)==f['source']['sha256']; checked_sources+=1
                    out={'file':t['source']['name'],'question':q['number'],'kind':t['kind'],'formula_id':fid,'format':f['format'],'fields':field_locations(q,fid),'location':f['location'],'reason':f.get('reason'),'fallback_error':f.get('fallback_error'),'source_evidence':str((taskroot/f['source']['path']).relative_to(ROOT)),'source_sha256':sha(evidence),'preview_binding':f.get('preview_binding'),'preview_available':bool(f.get('preview'))}
                    assert out['fields'], (key,fid)
                    reason_counts[f.get('reason')]+=1;fallback_counts[f.get('fallback_error')]+=1
                    reason_questions[f.get('reason')].add(key);fallback_questions[f.get('fallback_error')].add(key)
                    if f['format']=='mathtype':
                        rid=nodes[0].get('{'+R+'}id'); target=posixpath.normpath(posixpath.join('word',rels[rid])); assert z.read(target)==evidence
                        out['original_source_part']=target
                        if f.get('preview'):
                            pv=(taskroot/f['preview']['path']).read_bytes(); assert sha(pv)==f['preview']['sha256']; assert f['preview_binding']['verified']; assert z.read(f['preview_binding']['source_part'])==pv;checked_previews+=1
                            out['preview_evidence']=str((taskroot/f['preview']['path']).relative_to(ROOT));out['preview_sha256']=sha(pv)
                            out['original_preview_text_records']=wmf_text_records(pv)
                    else:
                        script_records+=1
                        run=nodes[0].getparent();align=run.find('w:rPr/w:vertAlign',namespaces=namespaces)
                        out['original_text']=nodes[0].text;out['original_vertAlign']=align.get('{'+W+'}val') if align is not None else None
                        assert out['original_vertAlign'] in ('subscript','superscript')
                    block=next(b for b in t['blocks'] if any(x.get('id')==fid for x in b['tokens']))
                    out['block_text']=block['text'];out['block_location']=block['location']
                    row['failed_formulas'].append(out); formulas.append(out)
                    reps.setdefault(f.get('fallback_error'),{k:out[k] for k in ('file','question','formula_id','fields','location','reason','fallback_error')})
                questions.append(row)
    report={'schema_version':1,'baseline':'outputs/bundled-all-nine','scope':'All 69 quarantined questions; parser behavior unchanged','documents':documents,'totals':{'documents':len(documents),'questions':sum(d['total'] for d in documents),'quarantined_questions':len(questions),'raw_issue_records':dict(raw_codes),'unique_failed_formula_objects':len(formulas),'mathtype_objects':checked_previews,'word_script_objects':script_records,'source_objects_sha_and_xpath_verified':checked_sources,'original_previews_bound_and_sha_verified':checked_previews},'conversion_reasons':[{'reason':k,'objects':v,'questions':len(reason_questions[k])} for k,v in reason_counts.most_common()],'preview_failure_reasons':[{'reason':k,'objects':v,'questions':len(fallback_questions[k])} for k,v in fallback_counts.most_common()],'representatives':list(reps.values()),'questions':questions,'notes':['Question counts across reasons overlap; do not sum to 69.','10 word_script issues duplicate the 10 formula_review synthetic script objects at the same source locations.','A declared font or charset does not establish which semantic glyph will be lost. No unsupported WMF has been rendered for the preview audit.','Source Word can be valid even when this converter cannot establish a reliable representation.']}
    (ROOT/'outputs/problem-preview-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report['totals'],ensure_ascii=False,indent=2))
    print(json.dumps(report['representatives'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
