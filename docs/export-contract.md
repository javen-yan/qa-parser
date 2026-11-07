# Excel 导出约定与验证边界

新版 `run` 会自动调用校验与部分导出，交付成功包和 `quarantine.json`，无需人工确认。下文的显式 `allow_partial` 是底层导出API的兼容边界；公式原图兜底不会解除题目结构错误。当前自动验证记录见 `automatic-processing.md`。

导出层使用小型第三方库 **XlsxWriter**（BSD-2-Clause，纯 Python），不调用 Office、不启动外部转换进程。通用 XLSX 序列化由库承担，业务字段映射、资源校验及部分导出规则由本项目负责。运行依赖应随未来发行包携带，不要求客户自行安装 Python。

官方资料：

- [write_string 与字符串长度限制](https://xlsxwriter.readthedocs.io/worksheet.html#worksheet-write-string)
- [许可证与再分发要求](https://xlsxwriter.readthedocs.io/license.html)

## 输入与输出

`export_task(task, task_dir, out_dir, allow_partial=False)` 接收 schema_version=1 的任务，产出新目录：

```
questions.xlsx
images/0001.png
report.json
```

任务的 `kind` 为 `choice` 或 `blank`。题目 `issues` 非空即待复核；导出器不会自行消除解析问题。未显式开启部分导出时，只要存在待复核题就拒绝导出。部分导出报告包括完整遗漏题号、问题列表和 Excel 行号与源题号对应关系。没有可通过题时拒绝生成空模板。

选择题默认列：ID、类型、困难程度、题目、选择项、答案、解析、sid、optionA、optionB、optionC、optionD、title、questionType。填空默认列：ID、考点、题目、答案。

`ID/sid/questionType` 暂空；`title` 同题干，`optionA～D` 同对应选项；`选择项` 按 A．内容逐行保存。`field_defaults` 按列名提供空值默认值；`column_fields` 将列名显式映射到题目字段；`required_columns` 验证已知的必填列。`choice_columns/blank_columns` 允许配置列顺序。真实系统导入契约尚未验证，不能把这些暂定约定称为实际系统支持。

所有单元格使用 `write_string` 写入。以等号开头的内容、LaTeX、数字外观的标识和 URL 均保持文本，不生成计算公式或链接。单元格超过 32767 字符（同时保守检查 UTF-16 单元数）即失败，不静默截断。

## 图片与发布完整性

优先使用每题 `asset_filenames` 清单，同时识别题目导出文本中对任务资源文件名的引用。只复制通过题目引用的资源；检查资源位于任务目录内、扩展名一致、SHA-256 正确。保持原始字节，不转码。未支持的格式由解析/校验层产生待复核问题。

导出先在同父目录临时目录完成校验和写入，再以独占方式创建目标目录，发布文件。目标已存在时拒绝覆盖；正常异常会清理本次临时及未完成输出。`report.json` 是完成标识；应用崩溃或断电留下的无报告目录不能认定为完整包，也不会被后续运行覆盖。

本层测试检查底层 XLSX XML、字面文本、模板列顺序、多小问内容、部分导出、遗漏报告、缺失/篡改图片、目录逃逸、单元格超长及已有结果保护。此测试不能替代目标系统导入或 Office/Windows 运行验收。
