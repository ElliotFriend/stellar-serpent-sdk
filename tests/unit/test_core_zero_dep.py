"""The zero-dependency boundary: `serpent` core imports stdlib and itself only.

serpent's central promise is that an authored contract carries no runtime
dependencies, so every module under `src/serpent/` -- `__init__.py`,
`val`/`types`/`errors`/`decorators`/`env`, AND the generated `_host` bindings --
may import nothing but the standard library and other serpent modules.

`serpent/spec/` and `serpent/testing/` are the two recorded exceptions (plan
Global Constraints; M1-F ruling E2): `spec/` REQUIRES `stellar_sdk` for XDR
section emission and is build-time-only, `testing/` requires it for ScVal
marshalling and RPC and is test-time-only, and both are therefore excluded from
the walk below. Each exclusion is only safe because the root package never
re-exports it -- `import serpent` must not be able to drag `stellar_sdk` in --
which is the second half of this file.

The walk is static (`ast`), not import-based: importing a module would only
prove that *its* imports resolve in an environment where stellar_sdk happens to
be installed, which is exactly the environment CI runs in.
"""

import ast
import pathlib
import subprocess
import sys

import pytest

import serpent

SRC = pathlib.Path(serpent.__file__).parent
#: The recorded exceptions; everything else under SRC is walked. `spec/`
#: (stellar_sdk for XDR sections, build-time only) and `testing/` (stellar_sdk
#: for ScVal marshalling + RPC, test-time only, ruling E2). Neither is
#: re-exported by the root package -- the second half of this file.
EXEMPT = (SRC / "spec", SRC / "testing")


def _core_modules() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if not any(e in p.parents for e in EXEMPT))


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
    assert not any(name.startswith(("spec/", "testing/")) for name in walked)
    # ... and that each exempt subpackage really exists, so the exclusions are
    # meaningful rather than vacuous.
    for exempt, probe in ((SRC / "spec", "typemap.py"), (SRC / "testing", "_scval.py")):
        assert (exempt / probe).is_file()


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
    # Task 3 review minor, hardened here (T5): the two absolute-spelling checks
    # above miss a RELATIVE import -- `from .spec import ...` or
    # `from . import spec` inside serpent/__init__.py would walk right through
    # them (only the subprocess probe in
    # test_importing_serpent_does_not_load_stellar_sdk would still catch it).
    # RED evidence (mutation note): verified by hand against three synthetic
    # source strings ("from .spec import x", "from . import spec",
    # "from serpent import spec") standing in for a hypothetical
    # serpent/__init__.py mutation -- for each, the two absolute-spelling
    # checks above are satisfied (silently missing the violation) while the
    # three checks below fail (catching it), confirming they are
    # load-bearing rather than redundant with the checks above.
    assert "from .spec" not in source
    assert "from . import spec" not in source
    assert "from serpent import spec" not in source


def test_serpent_testing_is_not_reachable_from_the_package_root() -> None:
    """The same five checks for the SECOND exemption (`serpent.testing`, M1-F
    ruling E2), which imports stellar_sdk at module import exactly as
    `serpent.spec` does. Spelled out rather than parametrized over the two
    names, because each assertion is a distinct source spelling and a failure
    should name the one that slipped through."""
    assert "testing" not in serpent.__all__
    source = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "from serpent.testing" not in source
    assert "import serpent.testing" not in source
    assert "from .testing" not in source
    assert "from . import testing" not in source
    assert "from serpent import testing" not in source


def test_importing_serpent_does_not_load_stellar_sdk() -> None:
    """The dynamic half of the boundary, in a fresh interpreter: this test
    process has already imported `serpent.spec` (test_typemap does), which sets
    `serpent.spec` as an attribute of the package, so the check is only
    meaningful out-of-process."""
    probe = (
        "import sys, serpent;"
        "assert 'stellar_sdk' not in sys.modules, 'serpent core pulled in stellar_sdk';"
        "assert 'serpent.spec' not in sys.modules, 'serpent core pulled in serpent.spec';"
        "assert 'serpent.testing' not in sys.modules, 'serpent core pulled in serpent.testing';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize("exempt", EXEMPT, ids=lambda d: d.name)
def test_each_exempt_subpackage_does_import_stellar_sdk(exempt: pathlib.Path) -> None:
    """Each exemption is real, not a formality: if `serpent.spec` or
    `serpent.testing` ever stops needing `stellar_sdk`, delete THAT exemption
    instead of keeping it.

    One assertion PER directory (review B7), not one over their union: a
    `testing/` that had quietly stopped importing `stellar_sdk` would still be
    covered by `spec/`'s import if the two were pooled, and the exemption it no
    longer needs would go on standing.
    """
    roots: set[str] = set()
    for path in sorted(exempt.rglob("*.py")):
        roots |= _imported_roots(path)
    assert "stellar_sdk" in roots
