from __future__ import annotations

import ast
from pathlib import Path

CORE_IMPORT_FORBIDDEN = "bwli.llm"
LLM_FORBIDDEN_IMPORTS = {"bwli.client", "bwli.endpoints", "bwli.snapshot"}
SRC_ROOT = Path("src")


def test_core_modules_do_not_import_llm() -> None:
    offenders: list[str] = []

    root = Path("src/bwli")
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] == "llm":
            continue
        offenders.extend(_find_forbidden_imports(path, {CORE_IMPORT_FORBIDDEN}))

    assert offenders == []


def test_llm_modules_do_not_import_bw_api_or_snapshot_boundaries() -> None:
    root = Path("src/bwli/llm")
    offenders: list[str] = []

    if root.exists():
        for path in root.rglob("*.py"):
            offenders.extend(_find_forbidden_imports(path, LLM_FORBIDDEN_IMPORTS))

    assert offenders == []


def test_relative_imports_are_normalized_for_llm_boundary_guard() -> None:
    module_node = ast.parse("from ..snapshot import SnapshotManifest").body[0]
    package_node = ast.parse("from .. import snapshot").body[0]

    assert "bwli.snapshot" in _imported_module_names(
        module_node, Path("src/bwli/llm/probe.py")
    )
    assert "bwli.snapshot" in _imported_module_names(
        package_node, Path("src/bwli/llm/probe.py")
    )


def _find_forbidden_imports(path: Path, forbidden_modules: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        for imported_name in _imported_module_names(node, path):
            if any(
                imported_name == forbidden
                or imported_name.startswith(f"{forbidden}.")
                for forbidden in forbidden_modules
            ):
                offenders.append(f"{path}:{node.lineno}")
    return offenders


def _imported_module_names(node: ast.AST, path: Path) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            resolved = _resolve_relative_import(path, node.level, module)
            names = [resolved] if resolved else []
            if not module:
                names.extend(f"{resolved}.{alias.name}" for alias in node.names if resolved)
            return names
        names = [module] if module else []
        if module == "bwli":
            names.extend(f"bwli.{alias.name}" for alias in node.names)
        return names
    return []


def _resolve_relative_import(path: Path, level: int, module: str) -> str:
    module_path = path.with_suffix("").relative_to(SRC_ROOT)
    package_parts = module_path.parts[:-1]
    keep_count = max(0, len(package_parts) - level + 1)
    base_parts = package_parts[:keep_count]
    suffix = tuple(part for part in module.split(".") if part)
    return ".".join((*base_parts, *suffix))
