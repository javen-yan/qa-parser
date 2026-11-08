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
images/aaa0001.png
report.json
```

任务的 `kind` 为 `choice` 或 `blank`。题目 `issues` 非空即待复核；导出器不会自行消除解析问题。未显式开启部分导出时，只要存在待复核题就拒绝导出。部分导出报告包括完整遗漏题号、问题列表和 Excel 行号与源题号对应关系。没有可通过题时拒绝生成空模板。

选择题默认列：ID、类型、困难程度、题目、选择项、答案、解析、sid、optionA、optionB、optionC、optionD、title、questionType。填空默认列：ID、考点、题目、答案。

`ID/sid/questionType` 暂空；`title` 同题干，`optionA～D` 同对应选项；`选择项` 按 A．内容逐行保存。`field_defaults` 按列名提供空值默认值；`column_fields` 将列名显式映射到题目字段；`required_columns` 验证已知的必填列。`choice_columns/blank_columns` 允许配置列顺序。真实系统导入契约尚未验证，不能把这些暂定约定称为实际系统支持。

所有单元格使用 `write_string` 写入。以等号开头的内容、LaTeX、数字外观的标识和 URL 均保持文本，不生成计算公式或链接。单元格超过 32767 字符（同时保守检查 UTF-16 单元数）即失败，不静默截断。

## 图片与发布完整性

默认图片引用严格使用`参考aaa0001.png)`，aaa 固定为小写，序号至少四位补零，结尾为 ASCII 右括号，不加左括号、“请”或空格；对应实际文件名仍为`aaa0001.png`，保存在本份文档的`images/aaa0001.png`。题干、选项、答案和解析共用`image_reference: "参考{filename})"`配置，离线预览按同一模板匹配相对图片。已有任务中的旧模板及显式自定义模板继续按其配置处理，不自动改写旧任务正文；批量导出中每份文档保留独立图片目录。

优先使用每题 `asset_filenames` 清单，同时识别题目导出文本中对任务资源文件名的引用。只复制通过题目引用的资源；检查资源位于任务目录内、扩展名一致、SHA-256 正确。保持原始字节，不转码。未支持的格式由解析/校验层产生待复核问题。

导出先在同父目录临时目录完成校验和写入，再以独占方式创建目标目录，发布文件。目标已存在时拒绝覆盖；正常异常会清理本次临时及未完成输出。`report.json` 是完成标识；应用崩溃或断电留下的无报告目录不能认定为完整包，也不会被后续运行覆盖。

本层测试检查底层 XLSX XML、字面文本、模板列顺序、多小问内容、部分导出、遗漏报告、缺失/篡改图片、目录逃逸、单元格超长及已有结果保护。此测试不能替代目标系统导入或 Office/Windows 运行验收。

## 统一客户交付布局（2026-11-08）

桌面 UI（单份或批量）、`batch` CLI 和 `run` CLI 的最终 ZIP 统一使用原 Word 基名，不再保留数字/非数字两套布局：

```text
1.xlsx
1/aaa0001.png
01.xlsx
01/aaa0001.png
物理 练习.xlsx
物理 练习/aaa0001.png
_说明/0001/结果说明.txt
_说明/0001/题目预览.html
_说明/0001/未导出题目.html  # 仅有未导出题时
_display/                 # 共享离线预览字体及许可证
批次结果说明.txt
```

前导零、中文和空格原样保留，不按上传顺序改名。无图片也保留同名空目录。图片引用仍为 `参考aaa0001.png)`，不添加目录前缀或新的导入语法。每份 Word 的说明映射写在批次说明中；辅助内容不放入同名图片文件夹，也不复制旧版 Excel/images 目录树。

UI 开始转换前、批量开始前、单文件 CLI 开始前及打包时均校验交付名称。重复基名、大小写/Unicode 规范等价名称、文件与目录路径冲突、保留辅助目录名、Windows 禁用名称/字符均明确拒绝，提示重命名，不覆盖、不自动附加流水号。内部诊断目录及底层 `parse`/`export` 结构继续使用 `questions.xlsx`、`images/`；它们不是客户最终包。

### 布局阶段本地证据（混合策略接入前）

- 59 项相关回归通过，覆盖 UI 实际 worker、单份 CLI、批量、重名/路径冲突、保留前导零、原图哈希及最终预览路径。
- 当前物理样本以 `1.docx` 转换，100 题全部导出；新布局最终包的真实浏览器检查为 123 张图片无失效、892 处公式均构建显示。
- 截图对应第 57、92 题的本地 Excel 解析字段与任务字段完全相同，不含 `<em>`。两题解析部分共有 51 段公式；包括题干和选项共有 67 个公式对象、25 种不同 LaTeX。
- 以上不是客户实际 Excel 与 APP 的验收；下划线、Markdown/HTML 及分隔符问题只作为待核实路径。详细分层证据及图片实验见 [公式交接排查](formula-handoff.md)。

当前混合策略及原生渲染验收进展见 [公式交接排查](formula-handoff.md) 的最后一节。布局阶段的纯公式显示数量不能当成混合版输出数量。
