"""Check the dependency direction between Yamicha package areas."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_DEPENDENCIES = {
    "contracts": {"contracts"},
    "life": {"contracts", "life"},
    "body": {"contracts", "body"},
    "adapters": {"contracts", "adapters"},
    "bootstrap": {"contracts", "life", "body", "adapters", "bootstrap"},
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    source_area: str
    target_area: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.source_area} must not import "
            f"yamicha.{self.target_area}"
        )


def _area_from_module(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "yamicha":
        return None
    return parts[1]


def _resolve_relative_module(
    current_package: str,
    module: str | None,
    level: int,
) -> str:
    package_parts = current_package.split(".")
    keep = max(0, len(package_parts) - (level - 1))
    target_parts = package_parts[:keep]
    if module:
        target_parts.extend(module.split("."))
    return ".".join(target_parts)


def _imported_modules(
    tree: ast.AST,
    current_package: str,
) -> list[tuple[str, int]]:
    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                resolved = node.module
            elif node.level > 0:
                resolved = _resolve_relative_module(
                    current_package,
                    node.module,
                    node.level,
                )
            else:
                continue
            modules.append((resolved, node.lineno))
            if resolved == "yamicha":
                modules.extend(
                    (f"yamicha.{alias.name}", node.lineno)
                    for alias in node.names
                    if alias.name != "*"
                )
    return modules


def find_violations(source_root: Path) -> list[Violation]:
    package_root = source_root / "yamicha"
    violations: list[Violation] = []
    if not package_root.exists():
        return violations

    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        if len(relative.parts) < 2:
            continue
        source_area = relative.parts[0]
        allowed = ALLOWED_DEPENDENCIES.get(source_area)
        if allowed is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_parts = list(relative.with_suffix("").parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
        else:
            module_parts.pop()
        current_package = ".".join(["yamicha", *module_parts])
        for module, line in _imported_modules(tree, current_package):
            target_area = _area_from_module(module)
            if target_area in ALLOWED_DEPENDENCIES and target_area not in allowed:
                violations.append(
                    Violation(path, line, source_area, target_area)
                )
    return violations


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    violations = find_violations(repository_root / "src")
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("architecture: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
