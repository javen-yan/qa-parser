#!/usr/bin/env python3
"""Verify explicit release artifacts and save hashes without changing them."""
from pathlib import Path
import hashlib,json
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
items=[]
paths=[ROOT/'dist/题库转换-macOS-arm64-轻量版.zip',ROOT/'dist/题库转换-macOS-arm64-原图完整版.zip',ROOT/'outputs/display-final-complete/batch/最终结果/题库最终结果.zip']
for p in paths:
 with ZipFile(p) as z:
  assert z.testzip() is None,p
  names=z.namelist()
  assert not any(n.startswith('__MACOSX/') or Path(n).name.startswith('._') for n in names),p
  if 'macOS' in p.name:
   assert any(n.endswith('/NotoSansCJKsc-Regular.otf') for n in names)
   assert any(n.endswith('/OFL.txt') for n in names)
   if '完整版' in p.name:
    assert any(n.endswith('/libreoffice/LibreOfficeDev.app/Contents/Resources/LICENSE') for n in names)
  else:
   assert sum(n.endswith('/questions.xlsx') for n in names)==9
   assert sum(n.endswith('/题目预览.html') for n in names)==9
   assert sum(n.endswith('/NotoSansCJKsc-Regular.otf') for n in names)==1
  items.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'entries':len(names),'zip_integrity':'passed'})
(ROOT/'outputs/display-release-artifacts.json').write_text(json.dumps(items,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(items,ensure_ascii=False,indent=2))
