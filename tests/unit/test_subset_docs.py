"""Task 12's byte-drift gate: `docs/subset.md` must equal its own generator.

`serpent.compiler._render_docs.render()` IS the specification (S14: "docs
generated from the truth, never hand-drifted") -- this test's only job is to
prove the checked-in `docs/subset.md` is exactly what running the generator
produces right now, so nobody can hand-edit the committed file and have it
silently drift from the registry/fixtures/recognition tables it claims to
describe.

Deliberately imports `serpent.compiler._render_docs` directly, never
`docs/gen_subset.py` (the CLI shim): the shim is untested by design (dossier
S14/task brief) so a bug hiding IN the shim can never mask a real doc/
generator mismatch here.
"""

from __future__ import annotations

from pathlib import Path

from serpent.compiler import _render_docs

#: `tests/unit/test_subset_docs.py` -> `tests/unit` -> `tests` -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUBSET_MD = _REPO_ROOT / "docs" / "subset.md"


def test_docs_subset_md_is_committed() -> None:
    assert _SUBSET_MD.is_file(), (
        f"{_SUBSET_MD} does not exist; generate it with "
        f"`{_render_docs.GENERATOR_COMMAND}` and commit it"
    )


def test_docs_subset_md_matches_its_generator_byte_for_byte() -> None:
    committed = _SUBSET_MD.read_bytes()
    regenerated = _render_docs.render().encode("utf-8")
    assert committed == regenerated, (
        "docs/subset.md has drifted from its generator -- regenerate it with "
        f"`{_render_docs.GENERATOR_COMMAND}` and commit the result"
    )


def test_docs_subset_md_names_its_own_generator() -> None:
    # The generated-file header (S14) must name the exact regeneration
    # command, so a reader who finds this file out of date knows what to run.
    header = _SUBSET_MD.read_text(encoding="utf-8").splitlines()[:8]
    assert any(_render_docs.GENERATOR_COMMAND in line for line in header), (
        "docs/subset.md's header must name its generator command"
    )


def test_render_is_deterministic_across_repeated_calls() -> None:
    # No timestamp, no absolute path, no unordered container: regenerating
    # twice in the same process must produce identical bytes (S14).
    assert _render_docs.render() == _render_docs.render()


def test_render_contains_no_absolute_path() -> None:
    # S14: only paths relative to tests/must_reject/ (e.g. "constructs/foo.py")
    # may appear -- never this checkout's own absolute filesystem path.
    rendered = _render_docs.render()
    assert str(_REPO_ROOT) not in rendered


def test_e3_note_renders_on_the_storage_keys_section() -> None:
    # Plan review minor 10 / dossier E3: struct storage keys are not modelled
    # in tier 1's ordering, and that fact must render on the storage-keys
    # section specifically, not just somewhere in the document.
    rendered = _render_docs.render()
    storage_keys_idx = rendered.index("#### Storage keys")
    next_section_idx = rendered.index("### 1.4 Containers")
    assert storage_keys_idx < next_section_idx
    section = rendered[storage_keys_idx:next_section_idx]
    assert "not modelled in tier 1" in section
    assert "E3" in section
