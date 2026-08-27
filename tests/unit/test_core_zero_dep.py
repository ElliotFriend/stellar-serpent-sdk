"""The zero-dependency boundary: `serpent` core imports stdlib and itself only.

serpent's central promise is that an authored contract carries no runtime
dependencies, so every module under `src/serpent/` -- `__init__.py`,
`val`/`types`/`errors`/`decorators`/`env`, AND the generated `_host` bindings --
may import nothing but the standard library and other serpent modules.

`serpent/spec/` is the one, recorded exception (plan Global Constraints): it
REQUIRES `stellar_sdk` for XDR section emission, is build-time-only, and is
therefore excluded from the walk below. The exclusion is only safe because the
root package never re-exports it -- `import serpent` must not be able to drag
`stellar_sdk` in -- which is the second half of this file.

The walk is static (`ast`), not import-based: importing a module would only
prove that *its* imports resolve in an environment where stellar_sdk happens to
be installed, which is exactly the environment CI runs in.
"""

import ast
import pathlib
import subprocess
import sys

import serpent

SRC = pathlib.Path(serpent.__file__).parent
#: The recorded exception; everything else under SRC is walked.
EXEMPT = SRC / "spec"


def _core_modules() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if EXEMPT not in p.parents)


def _imported_roots(path: pathlib.Path) -> set[str]:
    """Top-level module names imported by one file.

    A relative import (`from . import x`) has no top-level name to resolve and
    is serpent-internal by construction, so it is recorded as `serpent`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                roots.add("serpent")
            else:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_the_walk_actually_covers_the_core_modules() -> None:
    """Guard against a glob that silently matches nothing (or too little)."""
    walked = {p.relative_to(SRC).as_posix() for p in _core_modules()}
    assert "__init__.py" in walked
    assert "_host/__init__.py" in walked
    assert "_host/bindings.py" in walked
    assert "val.py" in walked
    assert "decorators.py" in walked
    assert "types/__init__.py" in walked
    assert not any(name.startswith("spec/") for name in walked)
    # ... and that the exempt subpackage really exists, so the exclusion is
    # meaningful rather than vacuous.
    assert (EXEMPT / "typemap.py").is_file()


def test_core_modules_import_only_stdlib_and_serpent() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _core_modules():
        foreign = {
            root
            for root in _imported_roots(path)
            if root != "serpent" and root not in sys.stdlib_module_names
        }
        if foreign:
            offenders[path.relative_to(SRC).as_posix()] = foreign
    assert offenders == {}


def test_serpent_spec_is_not_reachable_from_the_package_root() -> None:
    """`import serpent` must never pull in `serpent.spec` (hence stellar_sdk)."""
    assert "spec" not in serpent.__all__
    source = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "from serpent.spec" not in source
    assert "import serpent.spec" not in source


def test_importing_serpent_does_not_load_stellar_sdk() -> None:
    """The dynamic half of the boundary, in a fresh interpreter: this test
    process has already imported `serpent.spec` (test_typemap does), which sets
    `serpent.spec` as an attribute of the package, so the check is only
    meaningful out-of-process."""
    probe = (
        "import sys, serpent;"
        "assert 'stellar_sdk' not in sys.modules, 'serpent core pulled in stellar_sdk';"
        "assert 'serpent.spec' not in sys.modules, 'serpent core pulled in serpent.spec';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_spec_subpackage_does_import_stellar_sdk() -> None:
    """The exemption is real, not a formality: if `serpent.spec` ever stops
    needing `stellar_sdk`, delete the exemption instead of keeping it."""
    roots: set[str] = set()
    for path in sorted(EXEMPT.rglob("*.py")):
        roots |= _imported_roots(path)
    assert "stellar_sdk" in roots
