"""Small OS boundary for the local UI. No conversion logic lives here."""
from pathlib import Path
import os
import subprocess
import sys


def user_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QA Parser"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "QA Parser"


def open_local_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError("结果文件已移动或删除。")
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", str(path.resolve())], check=True, timeout=15)
    elif sys.platform == "win32":
        os.startfile(str(path.resolve()))
    else:
        raise RuntimeError("当前界面尚未验证此系统的打开文件操作。")
