"""Production code must not depend on `shipwright.evaluation` (test/eval support only)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "shipwright"


def imported_modules(tree: ast.AST) -> list[str]:
    """Dotted names imported by `tree`; `from a import b` yields both `a` and `a.b`."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            names.append(base)
            names += [f"{base}.{a.name}".lstrip(".") if node.module else a.name for a in node.names]
    return names


def touches_evaluation(name: str) -> bool:
    parts = name.lstrip(".").split(".")
    return parts[0] == "evaluation" or parts[:2] == ["shipwright", "evaluation"]


def test_detector_catches_every_import_form():
    for code in (
        "import shipwright.evaluation",
        "import shipwright.evaluation.fakes as f",
        "from shipwright.evaluation import fakes",
        "from shipwright.evaluation.fakes import FakeTool",
        "from shipwright import evaluation",
        "from shipwright import config, evaluation",
        "from . import evaluation",
        "from .evaluation import fakes",
    ):
        assert any(touches_evaluation(n) for n in imported_modules(ast.parse(code))), code
    for code in ("from shipwright import config", "import shipwright.config", "from . import x"):
        assert not any(touches_evaluation(n) for n in imported_modules(ast.parse(code))), code


def test_nothing_outside_evaluation_imports_it():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.relative_to(SRC).parts[0] == "evaluation":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(touches_evaluation(n) for n in imported_modules(tree)):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, offenders
