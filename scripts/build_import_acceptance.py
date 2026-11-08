#!/usr/bin/env python3
"""Generate six isolated import cases through the product's actual exporter."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,re,shutil,sys
from zipfile import ZipFile,ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qa_parser.core import validate_task
from qa_parser.exporter import export_task
from qa_parser.export_preview import render_export_preview,_workbook_rows
cases=[
 ('01-普通选择题','MathType公式化学2.docx','5','题干、四个选项、答案、解析和难度各归其列；无公式或图片。'),
 ('02-条件箭头','MathType公式化学2.docx','1','A、C 的点燃置于右箭头上方；D 的 Δ 置于右箭头上方；B 为普通右箭头。答案 B。'),
 ('03-中文下标','微软公式物理.docx','10','解析中的 U 总下标、U_L、U_R 及分式完整显示；总字不得与主行等高；四个电路图片选项分别关联。'),
 ('04-矩阵原图','MathType公式化学填空2.docx','62','一道大题一行；答案三张原图保留 Δ、点燃、双横线、上下标及 ↑。图片引用须在答案原位置对应；不得要求其成为可编辑公式。'),
 ('05-多图选择题','MathType公式化学2.docx','3','A、B、C、D 各自图片关联正确；序号属于本样本目录，不与其他文件的同名图片串用。'),
 ('06-多小问填空','MathType公式化学填空2.docx','1','一道大题一行；公共题干及 (1)～(4) 顺序、完整答案保留；考点为空。'),
]
out=ROOT/'outputs/import-acceptance';out.mkdir(exist_ok=False)
source_root=ROOT/'outputs/display-final-complete/batch'
tasks={}
for p in source_root.glob('*/task/task.json'):
 t=json.loads(p.read_text());tasks[t['source']['name']]=(p.parent,t)
manifest=[]
for name,filename,number,expected in cases:
 task_root,original=tasks[filename]
 validated=validate_task(original,task_root)
 assert not validated['document_issues']
 selected=[q for q in validated['questions'] if str(q['number'])==number]
 assert len(selected)==1 and not selected[0]['issues']
 task=deepcopy(original);task['questions']=selected
 folder=out/name
 report=export_task(task,task_root,folder)
 assert report['exported_questions']==1 and not report['omitted_questions']
 cells=_workbook_rows(folder/'questions.xlsx')
 assert len(cells)==2 and len(cells[0][1])==len(cells[1][1])
 # Cross-check all strings with the full frozen application's real workbook.
 full=_workbook_rows(task_root.parent/'export/questions.xlsx')
 full_report=json.loads((task_root.parent/'export/report.json').read_text())
 excel_row=next(r['excel_row'] for r in full_report['rows'] if str(r['number'])==number)
 assert cells[0][1]==full[0][1] and cells[1][1]==dict(full)[excel_row]
 html,stats=render_export_preview(folder,filename)
 (folder/'题目预览.html').write_text(html)
 for key,value in zip(cells[0][1],cells[1][1]):
  for image in re.findall(r'(?:请)?参考 ?(\d+\.[A-Za-z0-9]+)',value):
   assert (folder/'images'/image).is_file()
 (folder/'验收说明.txt').write_text(f'原文：{filename}，第 {number} 题\n导入位置：questions.xlsx，第 2 行\n预期：{expected}\n状态：待目标系统验证。\n')
 manifest.append({'case':name,'source':filename,'number':number,'kind':task['kind'],'excel_row':2,
  'expected':expected,'images':[x['filename'] for x in report['images']], 'preview':stats,
  'workbook_sha256':hashlib.sha256((folder/'questions.xlsx').read_bytes()).hexdigest()})
fonts=ROOT/'src/qa_parser/web/vendor/fonts';(out/'_display').mkdir()
for n in ('NotoSansCJKsc-Regular.otf','OFL.txt'):shutil.copyfile(fonts/n,out/'_display'/n)
text='目标系统导入验收样本：6 题，4 道选择题、2 道填空题。\n每个目录的 Excel 仅含表头及一条真实导出数据；图片编号保持与全量结果一致。请分别导入，保留图片所属目录。\n本包未上传到目标系统，题型/难度编码、必填列、图片关联和公式渲染均待验证。\n\n'+ '\n\n'.join(f"{c['case']}：{c['source']} 第 {c['number']} 题\n预期：{c['expected']}\n结果：待验证；记录导入条数、报错字段及系统内显示截图。" for c in manifest)
(out/'验收清单.txt').write_text(text)
(out/'样本映射.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
with ZipFile(out/'目标系统导入验收样本.zip','x',ZIP_DEFLATED) as z:
 for p in out.rglob('*'):
  if p.is_file() and p.suffix not in {'.zip','.json'}:z.write(p,p.relative_to(out))
with ZipFile(out/'目标系统导入验收样本.zip') as z:assert z.testzip() is None
print(json.dumps({'cases':len(manifest),'rows':6,'zip':str(out/'目标系统导入验收样本.zip'),'bytes':(out/'目标系统导入验收样本.zip').stat().st_size},ensure_ascii=False))
