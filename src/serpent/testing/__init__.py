"""serpent's test surface: the tiers that run a contract for real.

Deliberately EMPTY of re-exports for now -- Task 3 fills `__all__` with the
tier-2b/3 harness. Two rules hold for everything that lands here:

* it is `serpent[testing]`, not `serpent`. This subpackage imports
  `stellar_sdk` (and later a Rust extension), so it is the second recorded
  exemption from the zero-dep walk, alongside `serpent.spec`
  (`tests/unit/test_core_zero_dep.py`);
* `serpent/__init__.py` never imports it. `import serpent` must not be able to
  drag `stellar_sdk` in, which the same file's subprocess probe asserts.
"""
