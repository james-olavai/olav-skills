"""Deterministic Python parser AST validation; model generation belongs to the host."""
import ast as _ast

_ALLOWED_MODULES = frozenset({
    "json", "re", "networkx", "duckdb", "typing", "collections",
    "ipaddress", "dataclasses", "itertools", "logging",
    "netutils",
})

_BANNED_NAMES = frozenset({
    "__import__", "exec", "eval", "open", "compile", "input",
    "os", "sys", "subprocess", "shutil", "pathlib", "socket",
    "urllib", "requests", "httpx",
})


def ast_safety_check(code: str) -> tuple[bool, str]:
    """Parse Python source and reject dangerous constructs.

    Allow-list imports only; block ``__import__``/``exec``/``eval``/
    ``open``/``os``/``sys``/``subprocess``/``shutil``/``pathlib``/
    ``socket``/``urllib``/``requests``/``httpx`` etc. Used by the
    ARCH-25 PaC parser-learner to vet LLM-generated Python before
    sandbox execution.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_MODULES:
                    return False, f"import of {alias.name!r} not in allowlist"
        elif isinstance(node, _ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_MODULES:
                return False, f"from-import of {node.module!r} not in allowlist"
        elif isinstance(node, _ast.Name):
            if node.id in _BANNED_NAMES:
                return False, f"use of banned name {node.id!r}"
        elif isinstance(node, _ast.Attribute):
            root = node
            while isinstance(root, _ast.Attribute):
                root = root.value
            if isinstance(root, _ast.Name) and root.id in _BANNED_NAMES:
                return False, f"attribute access on banned name {root.id!r}"
        elif isinstance(node, _ast.Call):
            if isinstance(node.func, _ast.Name) and node.func.id in {
                "getattr", "setattr", "delattr", "globals", "locals", "vars",
            }:
                return False, f"call to {node.func.id!r} not allowed"
    return True, ""

