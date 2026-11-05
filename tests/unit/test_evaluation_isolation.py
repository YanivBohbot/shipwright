"""Production code must not depend on `shipwright.evaluation` (test/eval support only)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "shipwright"


def test_nothing_outside_evaluation_imports_it():
    offenders = []
    for path in SRC.rglob("*.py"):
        if "evaluation" in path.relative_to(SRC).parts[:1]:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.level and node.module is None:
                    names = [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(
                n == "evaluation" or "shipwright.evaluation" in n or n.startswith("evaluation.")
                for n in names
            ):
                offenders.append(str(path.relative_to(SRC)))
    assert not offenders, offenders
