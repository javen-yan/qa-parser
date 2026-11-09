#!/usr/bin/env python3
"""Native/frozen mixed-formula acceptance, synthetic input only; no release writes."""
import argparse,base64,hashlib,io,json,os,platform,re,shutil,subprocess,sys
from pathlib import Path
from zipfile import ZipFile
from xml.sax.saxutils import escape
from PIL import Image,ImageDraw
from windows_smoke import write_docx,paragraph,read_cells

ROOT=Path(__file__).resolve().parents[1]
CASES=[r'R_x=\frac{I_2R_0}{I_1}',r'\frac{a}{\frac{b}{c}}',r'\begin{pmatrix}a&b\\c&d\end{pmatrix}',
       r'A\xrightarrow{\text{加热}}B',r'\sqrt{x_1^2+x_2^2}',r'\text{电压}\ U=I_2R_0',r'x^^2']


def fixtures(root):
    root.mkdir(parents=True)
    atom=lambda s:'<m:r><m:t>'+escape(s)+'</m:t></m:r>'
    sub=lambda a,b:'<m:sSub><m:e>'+atom(a)+'</m:e><m:sub>'+atom(b)+'</m:sub></m:sSub>'
    frac=lambda a,b:'<m:f><m:num>'+a+'</m:num><m:den>'+b+'</m:den></m:f>'
    math=lambda s:'<m:oMath>'+s+'</m:oMath>'
    options=paragraph('A．甲 B．乙 C．丙 D．丁')+paragraph('【答案】A')
    q1='<w:p><w:r><w:t>1．求</w:t></w:r>'+math(frac(atom('U'),atom('I')))+'</w:p>'+options
    q2=paragraph('2．比较电阻')+options+'<w:p><w:r><w:t>【解析】</w:t></w:r>'+math(sub('R','x'))+'<w:r><w:t>与中文间隔后的</w:t></w:r>'+math(sub('R','0'))+'</w:p>'
    q3='<w:p><w:r><w:t>3．求</w:t></w:r>'+math(frac(sub('I','2')+sub('R','0'),sub('I','1')))+'</w:p>'+options
    q3+='<w:p><w:r><w:t>【解析】图示</w:t></w:r><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>'
    # An unverified source matrix must be quarantined, never redrawn from guessed TeX.
    bad='<w:p><w:r><w:t>4．未知源矩阵</w:t></w:r>'+math('<m:m><m:mr><m:e>'+atom('x')+'</m:e></m:mr></m:m>')+'</w:p>'+options
    im=Image.new('RGB',(50,35),'white');ImageDraw.Draw(im).rectangle((5,5,45,30),outline='blue',width=3)
    stream=io.BytesIO();im.save(stream,format='PNG')
    first=write_docx(root/'01.docx',q1+q2+q3+bad,stream.getvalue())
    second=write_docx(root/'物理 练习.docx',q1+q2+q3,stream.getvalue())
    return first,second


def execute(cmd,args,env,log,expected=0):
    child=dict(env,QA_PARSER_CLI_LOG=str(log))
    with log.open('w',encoding='utf-8') as stream:
        r=subprocess.run(cmd+list(map(str,args)),cwd=log.parent,env=child,stdout=stream,stderr=subprocess.STDOUT,timeout=240)
    if r.returncode!=expected:raise AssertionError(f'Exit {r.returncode} != {expected}: {log}: {log.read_text(encoding="utf-8", errors="replace")[-2500:]}')


def validate_batch(root):
    summary=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))
    assert summary['passed']==6 and summary['quarantined']==1 and summary['status']=='partial_failure',summary
    semantics=[]
    for row in summary['files']:
        work=Path(row['output']);task=json.loads((work/'task/task.json').read_text(encoding='utf-8'))
        q=task['questions']
        assert r'$$\frac{U}{I}$$' in q[0]['stem']
        assert '$${R}_{x}$$' in q[1]['analysis'] and '$${R}_{0}$$' in q[1]['analysis']
        assert '$$' in q[2]['stem'] and '参考aaa001.png)' in q[2]['analysis']
        assert all(f['status']=='converted' for f in task['formulas'] if f.get('latex'))
        assert len({a['filename'] for a in task['assets']})==1
        report=json.loads((work/'export/report.json').read_text(encoding='utf-8'))
        assert report['exported_questions']==3
        for image in report['images']:
            p=work/'export/images'/image['filename']
            assert hashlib.sha256(p.read_bytes()).hexdigest()==image['sha256']
            with Image.open(p) as img:img.verify()
        semantics.append({'name':row['name'],'cells':read_cells(work/'export/questions.xlsx'),
                          'policy':[(f['status'],f.get('output_policy')) for f in task['formulas']],
                          'omitted':[q['number'] for q in report['omitted_questions']]})
    with ZipFile(summary['delivery']) as z:
        names=z.namelist();assert z.testzip() is None
        assert {'01.xlsx','01/001.png','物理 练习.xlsx','物理 练习/001.png'}<=set(names)
        assert not any('/images/' in n or n.endswith('/questions.xlsx') for n in names)
        for i,stem in enumerate(['01','物理 练习'],1):
            from urllib.parse import quote
            html=z.read(f'_说明/{i:04d}/题目预览.html').decode()
            assert f'src="../../{quote(stem,safe="")}/001.png"' in html
            assert 'url(../../_display/' in html
    return semantics


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    inputs=fixtures(out/'中文 输入')
    modes={'development':[sys.executable,'-m','qa_parser']}
    if args.exe:modes['frozen']=[str(args.exe.resolve()),'--cli']
    report={'status':'failed','platform':platform.platform(),'customer_acceptance':'unverified','runs':{}}
    try:
        for mode,cmd in modes.items():
            work=out/mode;work.mkdir();env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
            if mode=='development':
                env['PYTHONPATH']=str(ROOT/'src');env['QA_PARSER_WINDOWS_RESOURCES']=str(ROOT/'build/windows/resources')
            else:
                for key in list(env):
                    if key.startswith(('PYTHON','QA_PARSER_')):env.pop(key)
                # os.environ is case-insensitive on Windows; a plain dict is not.
                system_root=os.environ.get('SystemRoot', r'C:\Windows')
                env['PATH']=os.pathsep.join([str(Path(system_root)/'System32'),system_root]) if sys.platform=='win32' else '/usr/bin:/bin:/usr/sbin:/sbin'
            request=work/'formulas.json';request.write_text(json.dumps(CASES), encoding='utf-8');result=work/'raster.json'
            execute(cmd,['_rasterize-formulas',request,result],env,work/'raster.log')
            raster=json.loads(result.read_text(encoding='utf-8'));assert len(raster)==len(CASES)
            assert all(r['status']=='converted' for r in raster[:-1]),raster
            assert raster[-1]['status']=='failed'
            assert len({r['png_sha256'] for r in raster[:-1]})==len(CASES)-1,'Stale snapshot reused'
            for index,row in enumerate(raster[:-1]):
                from qa_parser.formula_raster import validate_png
                validate_png((work/f'{index}.png').read_bytes())
            batch=work/'批量 结果'
            execute(cmd,['batch',*inputs,'--out',batch],env,work/'batch.log',expected=2)
            semantics=validate_batch(batch)
            duplicates=work/'duplicates';duplicates.mkdir();duplicate=duplicates/'01.docx';shutil.copyfile(inputs[0],duplicate)
            execute(cmd,['batch',inputs[0],duplicate,'--out',work/'不可覆盖'],env,work/'duplicate.log',expected=1)
            assert not (work/'不可覆盖').exists()
            # Child helper must fail visibly if pinned runtime is unavailable,
            # rather than silently using system Chrome or keeping risky TeX.
            report['runs'][mode]={'semantics':semantics,'raster':raster,'duplicates':'rejected_before_writes'}
        if args.exe:assert report['runs']['development']['semantics']==report['runs']['frozen']['semantics']
        report['status']='passed'
    finally:
        (out/'mixed-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
        print('MIXED_REPORT '+json.dumps(report,ensure_ascii=False),flush=True)
        for mode in modes:
            for index in [0,2,3,5]:
                p=out/mode/f'{index}.png'
                if p.exists():print('MIXED_VISUAL '+json.dumps({'mode':mode,'case':index,'png':base64.b64encode(p.read_bytes()).decode()}),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
