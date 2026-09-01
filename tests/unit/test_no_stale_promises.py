"""Task 10's promise-sweep gate: `git grep -n "sub-plan E" src tests` finds
ONLY mentions this sweep deliberately kept.

Every docstring/message that once said "sub-plan E will..." has to be one of
three things by the time the sub-plan closes: IMPLEMENTED (the promise is
kept, so the text should say so, not "will"), REPOINTED (the decision moved
to M2 or sub-plan F, and the text now names that), or REMOVED (the promise was
genuinely stale and nothing replaced it). This test is the net: it walks
`src/` and `tests/` (the only trees the sweep touched -- `docs/superpowers/`'s
decisions, dossiers and plans are deliberately never in scope, since they are
a historical record of the PLANNING, not a promise the shipped code makes) and
fails on any "sub-plan E" mention -- CASE-INSENSITIVELY, so a sentence-initial
"Sub-plan E will..." cannot slip past by capitalization alone -- that is not
one of the allowlist's DELIBERATE historical records below.

A historical record reads in the past tense about something that already
happened -- "was retired", "used to pin", "the whole of M1-C" -- never a
forward-looking "will"/"is deferred to"/"until" naming sub-plan E. Those got
implemented or repointed at M2/F by this same commit; see
`docs/superpowers/sdd/2026-08-28-m1e-env-runtime/task-10-report.md` for the
resolution table.
"""

from __future__ import annotations

from pathlib import Path

#: `tests/unit/test_no_stale_promises.py` -> `tests/unit` -> `tests` -> root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WALKED = (_REPO_ROOT / "src", _REPO_ROOT / "tests")

#: Matched case-INSENSITIVELY (see `_mentions`): "Sub-plan E" (sentence-
#: initial, as `env.py`'s own module docstring spells it) is the same promise
#: as "sub-plan E" and must not be invisible to this gate.
_NEEDLE = "sub-plan e"

#: This file itself, excluded from its own walk: its docstrings and comments
#: discuss "sub-plan E" throughout (that is the whole point of the file), and
#: none of that is the promise the walk is checking for.
_SELF = Path(__file__).resolve()

#: `(path relative to the repo root, 1-based line number)` for every mention
#: the sweep audited and chose to KEEP, with why in a trailing comment. A
#: mention landing here is never a live promise: it is either an append-only
#: registry's own history (D9 -- the row cannot be reworded away) or a test's
#: own past-tense account of what it used to pin.
_ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        # NO_FIXTURE_REASONS["SPT1032"]: the append-only registry's (D9) own
        # record of WHEN and WHY the code was retired -- not a promise.
        ("src/serpent/compiler/codes.py", 1088),
        # "the runtime half used to pin that the widened signatures reached
        # the sub-plan E stub" -- past tense, about a test's own history.
        ("tests/unit/test_decorators.py", 780),
        # "Rewritten (never deleted) from the assertion that both bodies
        # raised NotImplementedError("sub-plan E")." -- past tense.
        ("tests/unit/test_address.py", 139),
        # "was SPT1032 ... for the whole of M1-C" -- past tense, module intro.
        ("tests/unit/test_frontend_events.py", 3),
        # Pins the (deliberately kept) NO_FIXTURE_REASONS string above --
        # itself a fact about the frozen registry, not a promise.
        ("tests/unit/test_frontend_events.py", 138),
        # This table's own provenance note: "ruling E9 called for a SECOND
        # table, owned by sub-plan E, of STATEFUL scenarios" -- states which
        # sub-plan built the module, not what it will do later.
        ("tests/semantics/env_scenarios.py", 8),
        # Sentence-initial capitalization: "Sub-plan E gave every method here
        # an in-memory body" -- past tense, the module's own honest-boundary
        # disclaimer about what already happened, not a forward promise.
        ("src/serpent/env.py", 8),
    }
)


def _mentions(root: Path) -> list[tuple[str, int, str]]:
    """`(relative path, 1-based line number, line text)` for every line under
    `root` containing `_NEEDLE` CASE-INSENSITIVELY, `__pycache__` excluded."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or path.resolve() == _SELF:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if _NEEDLE in line.lower():
                found.append((str(path.relative_to(_REPO_ROOT)), lineno, line.strip()))
    return found


def _all_mentions() -> list[tuple[str, int, str]]:
    mentions: list[tuple[str, int, str]] = []
    for root in _WALKED:
        mentions.extend(_mentions(root))
    return mentions


def test_every_sub_plan_e_mention_is_an_allowlisted_historical_record() -> None:
    mentions = _all_mentions()
    found = {(path, lineno) for path, lineno, _line in mentions}
    unexpected = sorted(found - set(_ALLOWLIST))
    assert not unexpected, (
        'unexpected "sub-plan E" mention(s) outside the allowlist -- each one has to '
        "be IMPLEMENTED, REPOINTED at M2/F, REMOVED, or (only if it is a genuine "
        f"historical record) added to _ALLOWLIST with why: {unexpected}"
    )


def test_the_allowlist_names_no_line_that_has_moved_or_changed() -> None:
    """The other direction: an allowlisted line that drifted (moved, or lost
    its "sub-plan E" text) would silently stop exempting anything, which is
    harmless, but ALSO stop being verified -- so a stale entry is a bug in
    this test, not a pass."""
    found = {(path, lineno) for path, lineno, _line in _all_mentions()}
    missing = sorted(set(_ALLOWLIST) - found)
    assert not missing, (
        f'allowlisted location(s) no longer contain "sub-plan E" -- the text moved or '
        f"changed; update the allowlist to match: {missing}"
    )
