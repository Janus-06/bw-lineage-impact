from __future__ import annotations

import ast
from pathlib import Path

CORE_IMPORT_FORBIDDEN = "bwli.llm"


def test_core_modules_do_not_import_llm() -> None:
    root = Path("src/bwli")
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] == "llm":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == CORE_IMPORT_FORBIDDEN or alias.name.startswith(
                        f"{CORE_IMPORT_FORBIDDEN}."
                    ):
                        offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == CORE_IMPORT_FORBIDDEN or module.startswith(
                    f"{CORE_IMPORT_FORBIDDEN}."
                ):
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []
