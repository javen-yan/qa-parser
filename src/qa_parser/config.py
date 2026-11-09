"""Import contract is explicit and provisional until tested against the target app."""
from copy import deepcopy
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "schema_version": 1,
    "contract_status": "unverified",
    "choice_columns": ["ID", "类型", "困难程度", "题目", "选择项", "答案", "解析", "sid", "optionA", "optionB", "optionC", "optionD", "title", "questionType"],
    "blank_columns": ["ID", "考点", "题目", "答案"],
    "choice_type": "单选题",
    "question_type": "",
    "formula_delimiter": "$$",
    "formula_fallback": "image",
    "image_reference": "参考aaa{filename})",
    "difficulty_levels": ["易", "中", "难"],
    "difficulty_seed": "qa-parser-v1",
    "field_defaults": {},
    "column_fields": {},
    "required_columns": [],
    "required_fields": {"choice": ["stem", "options", "answer"], "blank": ["stem", "answer"]},
    "supported_image_extensions": ["png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"],
}


def load_config(path: Path | None = None) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    if path:
        supplied = json.loads(path.read_text(encoding="utf-8"))
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        config.update(supplied)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported configuration schema_version")
    # Until real system acceptance is recorded, configuration cannot assert acceptance.
    if config.get("contract_status") != "unverified":
        raise ValueError("This release only supports contract_status=unverified")
    for name in ("choice_columns", "blank_columns", "difficulty_levels"):
        values = config.get(name)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values) or len(values) != len(set(values)):
            raise ValueError(f"Invalid configuration {name}")
    if len(config["difficulty_levels"]) != 3:
        raise ValueError("Exactly three difficulty levels are required")
    if not isinstance(config.get("formula_delimiter"), str) or not config["formula_delimiter"]:
        raise ValueError("formula_delimiter must be nonempty text")
    if config.get("formula_fallback", "isolate") not in ("image", "isolate"):
        raise ValueError("formula_fallback must be image or isolate")
    if config["image_reference"].count("{filename}") != 1:
        raise ValueError("image_reference must contain exactly one {filename}")
    config["image_reference"].format(filename="0001.png")
    if not isinstance(config.get("field_defaults"), dict):
        raise ValueError("field_defaults must be an object")
    if not isinstance(config.get("column_fields"), dict) or any(value not in {"stem", "answer", "analysis", "difficulty", "knowledge_point", "number", "id"} for value in config["column_fields"].values()):
        raise ValueError("column_fields must map columns to scalar question fields")
    if not isinstance(config.get("required_columns"), list) or not all(isinstance(c, str) for c in config["required_columns"]):
        raise ValueError("required_columns must be a list of column names")
    for kind in ("choice", "blank"):
        fields = config.get("required_fields", {}).get(kind)
        if not isinstance(fields, list) or any(f not in {"stem", "options", "answer", "analysis", "difficulty", "knowledge_point"} for f in fields):
            raise ValueError(f"Invalid required_fields.{kind}")


def digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def config_digest(config: dict) -> str:
    return digest(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8"))
