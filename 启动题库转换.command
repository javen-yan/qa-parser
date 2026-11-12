#!/bin/zsh
project_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$project_dir" || exit 1
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  print '无法启动：程序文件不完整，请联系维护人员。'
  read -k 1
  exit 1
fi
print '正在打开题库转换独立窗口。'
"$project_dir/.venv/bin/python" -m qa_parser desktop
