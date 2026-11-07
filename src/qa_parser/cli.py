"""CLI exit codes: 0 success, 2 review/partial, 1 execution failure."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

from .config import DEFAULT_CONFIG, load_config
from .core import export_document, load_task, parse_document, validate_task, run_document
from .result import result_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线 DOCX 题库转换内核；导入契约尚未验证")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("desktop", help="打开独立桌面窗口")
    ui = commands.add_parser("ui", help="打开本地题库转换界面")
    ui.add_argument("--out", type=Path, help="默认结果保存目录")
    ui.add_argument("--data-dir", type=Path, help=argparse.SUPPRESS)
    ui.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    ui.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    batch = commands.add_parser("batch", help="批量转换 Word，可自动识别正文题型")
    batch.add_argument("sources", type=Path, nargs="+")
    batch.add_argument("--mode", choices=["auto", "choice", "blank"], default="auto")
    batch.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="全自动转换：可靠公式或原图兜底，导出成功题目并交付隔离清单")
    run.add_argument("source", type=Path)
    run.add_argument("--kind", choices=["choice", "blank"], required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    run.add_argument("--json", action="store_true", help="开发调试：输出机器可读诊断摘要")
    parse = commands.add_parser("parse", help="解析一份 DOCX，保存中间结构、资源及隔离原因")
    parse.add_argument("source", type=Path)
    parse.add_argument("--kind", choices=["choice", "blank"], required=True)
    parse.add_argument("--out", type=Path, required=True)
    parse.add_argument("--config", type=Path)
    validate = commands.add_parser("validate", help="校验任务和资源完整性")
    validate.add_argument("task", type=Path)
    validate.add_argument("--source", type=Path, help="可选：与当前原始 DOCX 比较指纹")
    export = commands.add_parser("export", help="导出 Excel、图片和遗漏报告")
    export.add_argument("task", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--allow-partial", action="store_true", help="明确只导出已通过题目并列明遗漏")
    commands.add_parser("config", help="打印默认配置（可保存为 JSON 后调整）")
    args = parser.parse_args(argv)
    try:
        if args.command == "desktop":
            from .desktop_window import main as desktop_main
            return desktop_main([])
        if args.command == "ui":
            from .local_ui import serve
            return serve(args.data_dir, args.out, args.port, not args.no_browser)
        if args.command == "batch":
            from .batch import run_batch
            report = run_batch(args.sources, args.mode, args.out)
            print(report.get("message", report["status"]))
            print(f"最终结果：{report.get('delivery') or report.get('delivery_report') or '未生成'}")
            print(f"诊断目录：{args.out.resolve()}")
            return {"success": 0, "partial_failure": 2, "failure": 1}.get(report["status"], 1)
        if args.command == "config":
            print(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run":
            report = run_document(args.source, args.kind, args.out, load_config(args.config))
        elif args.command == "parse":
            report = parse_document(args.source, args.kind, args.out, load_config(args.config))
        elif args.command == "validate":
            task, root = load_task(args.task)
            report = validate_task(task, root, args.source)
        else:
            report = export_document(args.task, args.out, args.allow_partial)
        if args.command == "run":
            result = report["user_result"]
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(result_text(result, brief=True), end="")
                print(f"结果说明：{(args.out / 'result.txt').resolve()}")
                if result["export"]:
                    print(f"导出文件：{(args.out / 'export').resolve()}")
                print(f"诊断报告：{(args.out / 'quarantine.json').resolve()}")
            return {"success": 0, "partial_failure": 2, "failure": 1}[result["status"]]
        # Detailed per-question reports remain in the task/export directory.
        summary = {k: v for k, v in report.items() if k not in {"questions", "assets", "rows", "images", "omitted_questions"}}
        if "omitted_questions" in report:
            summary["omitted_count"] = len(report["omitted_questions"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if report.get("status") == "failed":
            return 1
        return 2 if report.get("quarantined") or report.get("review", 0) or report.get("document_issues") or report.get("partial") or report.get("omitted_questions") else 0
    except (Exception,) as exc:
        if os.environ.get('QA_PARSER_CLI_LOG'):
            traceback.print_exc(file=sys.stderr)
        if args.command == "run" and not args.json:
            message = "输出位置已存在，请选择新的输出位置。" if isinstance(exc, FileExistsError) else "无法读取输入或写入输出，请检查文件和保存位置。" if isinstance(exc, OSError) else "转换未能完成。"
            print("转换失败：" + message, file=sys.stderr)
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".log", prefix="qa-parser-error-", delete=False) as log:
                    log.write(traceback.format_exc())
                    diagnostic = log.name
                print(f"诊断报告：{diagnostic}", file=sys.stderr)
            except OSError:
                print("诊断文件无法保存。", file=sys.stderr)
            return 1
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
