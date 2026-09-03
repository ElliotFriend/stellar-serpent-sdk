"""The promise-sweep gates: the tree makes no promise it has already kept.

THREE nets. The first two (M1-E/M1-E2 Task 10) walk `src/` and `tests/`. The
first is about the phrase "sub-plan E": every mention has to be a deliberate
historical record. The second is about the tagged-union / int-enum SURFACE: no
docstring, comment or message may still say that surface is unavailable, now
that `@contractunion` and `@contractenum` ship. The third (M1-F Task 10) is
about "sub-plan F", "tier 2b"/"tier-2b" and the possessive "F's", walked over
the WIDER `src/`, `tests/`, `examples/` and `docs/` (see `_WALKED_F` below).

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

import re
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

#: `(path relative to the repo root, the mention's STRIPPED LINE TEXT)` for
#: every mention the sweep audited and chose to KEEP, with why in a trailing
#: comment. A mention landing here is never a live promise: it is either an
#: append-only registry's own history (D9 -- the row cannot be reworded away)
#: or a test's own past-tense account of what it used to pin.
#:
#: **Keyed on the TEXT, not the line number** (review m15, the G-item B2 this
#: migration pays off). A line-numbered allowlist has to be renumbered by every
#: edit ABOVE it, in files nobody was otherwise touching: M1-F's own metadata
#: edit to `env_scenarios.py` would have moved its entry, and the second test
#: below would have failed for a reason that has nothing to do with a stale
#: promise. The text key moves with the line for free and is STRICTER about
#: what it exempts -- an entry stops matching the moment the sentence itself is
#: reworded, which is exactly when it should be re-audited.
_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # NO_FIXTURE_REASONS["SPT1032"]: the append-only registry's (D9) own
        # record of WHEN and WHY the code was retired -- not a promise.
        (
            "src/serpent/compiler/codes.py",
            '"retired by M1-E (sub-plan E): the form it rejected is now supported -- "',
        ),
        # "the runtime half used to pin that the widened signatures reached
        # the sub-plan E stub" -- past tense, about a test's own history.
        (
            "tests/unit/test_decorators.py",
            "that the widened signatures reached the sub-plan E stub, and now pins that",
        ),
        # "Rewritten (never deleted) from the assertion that both bodies
        # raised NotImplementedError("sub-plan E")." -- past tense.
        (
            "tests/unit/test_address.py",
            '`NotImplementedError("sub-plan E")`. `require_auth()` takes no `Env` -- the',
        ),
        # "was SPT1032 ... for the whole of M1-C" -- past tense, module intro.
        (
            "tests/unit/test_frontend_events.py",
            '`<Event instance>.publish(env)` was `SPT1032` ("deferred to sub-plan E") for',
        ),
        # Pins the (deliberately kept) NO_FIXTURE_REASONS string above --
        # itself a fact about the frozen registry, not a promise.
        (
            "tests/unit/test_frontend_events.py",
            'assert "sub-plan E" in codes.NO_FIXTURE_REASONS["SPT1032"]',
        ),
        # This table's own provenance note: "ruling E9 called for a SECOND
        # table, owned by sub-plan E, of STATEFUL scenarios" -- states which
        # sub-plan built the module, not what it will do later.
        (
            "tests/semantics/env_scenarios.py",
            "called for a SECOND table, owned by sub-plan E, of STATEFUL scenarios. This is",
        ),
        # Sentence-initial capitalization: "Sub-plan E gave every method here
        # an in-memory body" -- past tense, the module's own honest-boundary
        # disclaimer about what already happened, not a forward promise.
        (
            "src/serpent/env.py",
            "**This module is also a model, and a model is not an oracle.** Sub-plan E gave",
        ),
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


def _keyed() -> set[tuple[str, str]]:
    """Every mention as its allowlist key: `(relative path, stripped text)`.

    The LINE NUMBER stays in `_mentions`' answer -- a failure message that
    cannot say where the line is would be a worse gate -- and is dropped only
    here, where the comparison happens.
    """
    return {(path, line) for path, _lineno, line in _all_mentions()}


def test_every_sub_plan_e_mention_is_an_allowlisted_historical_record() -> None:
    unexpected = sorted(
        (path, lineno, line)
        for path, lineno, line in _all_mentions()
        if (path, line) not in _ALLOWLIST
    )
    assert not unexpected, (
        'unexpected "sub-plan E" mention(s) outside the allowlist -- each one has to '
        "be IMPLEMENTED, REPOINTED at M2/F, REMOVED, or (only if it is a genuine "
        f"historical record) added to _ALLOWLIST with why: {unexpected}"
    )


def test_the_allowlist_names_no_line_that_has_moved_or_changed() -> None:
    """The other direction: an allowlisted line that lost its "sub-plan E" text
    -- or was reworded, or deleted -- would silently stop exempting anything,
    which is harmless, but ALSO stop being verified, so a stale entry is a bug
    in this test rather than a pass.

    Keyed on the TEXT (review m15), so this no longer fires for the one
    non-reason a line-numbered allowlist fires for: an unrelated edit higher up
    the same file.
    """
    missing = sorted(set(_ALLOWLIST) - _keyed())
    assert not missing, (
        f'allowlisted line(s) no longer appear with their "sub-plan E" text -- the '
        f"sentence was reworded or removed; re-audit it and update the allowlist: "
        f"{missing}"
    )


# --- the M1-E2 surface-availability gate ------------------------------------
#
# The same two trees, a different promise. Before M1-E2 there was no way to
# declare a discriminated value at all (dossier SS C.2: `token_style.py`'s
# `@contracttype` storage key models keyspace discrimination, not a tagged-union
# VALUE, so the tree carried no union pattern to point at), and several places
# said so. Every one of those sentences is now false, and a false "not
# supported" is worse than no sentence: it sends an author to a workaround for
# a feature that shipped.
#
# `docs/superpowers/` is deliberately NOT walked here, for the same reason the
# gate above skips it: its decisions, dossiers and plans are a record of the
# PLANNING, and "until it lands, the workaround stays ..." is true history
# there.

#: How the surface is named, in prose (`tagged union`, `int enum`) or in code
#: (the decorators, the bases, the two case factories). A line has to name the
#: surface before a denial on it can be about the surface at all -- which is
#: what keeps this gate off the many honest sentences about `X | None`
#: (`typing`'s "union", a different thing entirely).
_SURFACE = (
    r"(?:@?contractunion|@?contractenum|ContractUnion|ContractEnum"
    r"|tagged[- ]union|int[- ]enum|variant\(|enumvalue\()"
)

#: An UNQUALIFIED claim that something is absent from the toolchain. Chosen to
#: be the vocabulary an absence note actually uses; a claim that names WHERE
#: the thing went instead is handled by `_REPOINTED` below.
_DENIAL = (
    r"(?:not (?:yet )?(?:supported|available|implemented|modelled|modeled|in the subset)"
    r"|unsupported|unavailable|no support for|has no support"
    r"|(?:do|does|did|would) not support|(?:cannot|can\'t|could not) be declared"
    r"|will (?:be supported|be added|ship|land|come)|coming soon"
    r"|(?:is|are|was|were) (?:deferred|a stub|still a stub)|defers to"
    r"|(?:the )?documented workaround)"
)

#: Within one line and one clause, in either order: the surface may be named
#: before the denial ("a tagged union is not supported") or after it ("no
#: support for tagged unions"). The window keeps the two adjacent, so a line
#: that happens to mention both a union and, separately, something unsupported
#: is not a hit.
_CLAIM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(_SURFACE + r"[^.;]{0,60}?" + _DENIAL, re.IGNORECASE),
    re.compile(_DENIAL + r"[^.;]{0,60}?" + _SURFACE, re.IGNORECASE),
)

#: The one legitimate reason a line may pair the surface with a denial: it
#: names where the thing actually is. A REPOINT ("cross-contract union
#: arguments are M2", "not modelled in tier 1") is one of the three outcomes
#: the sweep above allows and the README's own fence table states -- it is not
#: a stale promise, and refusing it would force the honest scope fence out of
#: the code entirely. What this gate is for is the UNQUALIFIED denial, which
#: names nothing and leaves an author with nowhere to go.
_REPOINTED = re.compile(r"\b(?:M2|M3|sub-plan F|tier 1|tier 2[ab]?)\b")


def _is_a_surface_denial(line: str) -> bool:
    """Whether one line claims the surface is unavailable and names no repoint.

    The whole gate is this predicate; the walk below only feeds it. Pulled out
    so `test_the_surface_gate_has_teeth` can exercise it on both directions
    directly -- a gate over a tree that is already clean passes vacuously, and
    a vacuous pass is not evidence that it would catch anything.
    """
    if not any(claim.search(line) for claim in _CLAIM_RES):
        return False
    return not _REPOINTED.search(line)


def _surface_denials() -> list[tuple[str, int, str]]:
    """`(relative path, 1-based line number, line text)` for every line under
    the walked trees claiming the union/int-enum surface is unavailable and
    naming no repoint."""
    found: list[tuple[str, int, str]] = []
    for root in _WALKED:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path.resolve() == _SELF:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, start=1):
                if _is_a_surface_denial(line):
                    found.append((str(path.relative_to(_REPO_ROOT)), lineno, line.strip()))
    return found


#: Absence prose the gate MUST catch -- the four spellings a note about this
#: surface actually used, or would use.
_DENIALS_THAT_MUST_TRIP: tuple[str, ...] = (
    '"""A tagged union is not yet supported by the serpent subset."""',
    "# no support for @contractunion values; use a struct key instead",
    '"""The documented workaround for a tagged union stays a Symbol constant."""',
    "# an int enum is unavailable today",
    # The M1-E2 final review widened the vocabulary: five more spellings an
    # absence note reaches for, each of which used to walk straight past.
    "# we do not support @contractenum members with implicit discriminants",
    "# a tagged union cannot be declared in this position yet",
    "# variant() payloads of this shape will be supported in a later release",
    "# int-enum ordering is coming soon",
    "# the @contractunion decorator is a stub",
)

#: Prose the gate MUST NOT touch. The first two are the honest scope fence the
#: README states and the tree repeats (a repoint names its milestone or its
#: tier); the third is `spec/typemap.py`'s sentence about `X | None`, where
#: "union" means the TYPING construct and not this surface at all; the fourth
#: is ordinary prose that names the surface and denies nothing.
_PROSE_THAT_MUST_PASS: tuple[str, ...] = (
    "# cross-contract union arguments are deferred to M2",
    '"""A union as a Map key is not modelled in tier 1 at any entry count."""',
    "# a union that is not exactly one type plus `None` is unsupported",
    "# @contractunion and @contractenum both ship; variant() declares a case",
)


def test_the_surface_gate_has_teeth() -> None:
    """Both directions of the predicate, pinned as samples.

    By the time this landed the tree was already clean (Tasks 2 and 3 reworded
    the two `typemap` explanations and the four "exactly one of" strings as
    they widened them), so the walk below passes on an empty result set. That
    is the right outcome and NO evidence at all about whether the gate works
    -- these samples are that evidence.
    """
    missed = [line for line in _DENIALS_THAT_MUST_TRIP if not _is_a_surface_denial(line)]
    assert not missed, f"the gate would not catch these absence claims: {missed}"
    tripped = [line for line in _PROSE_THAT_MUST_PASS if _is_a_surface_denial(line)]
    assert not tripped, f"the gate false-positives on honest prose: {tripped}"


def test_no_source_or_test_file_says_the_union_surface_is_unavailable() -> None:
    """`@contractunion`/`@contractenum` ship, so nothing in `src/` or `tests/`
    may still say otherwise without naming where the missing part went."""
    denials = _surface_denials()
    assert not denials, (
        "line(s) claiming the tagged-union / int-enum surface is unavailable -- the "
        "surface shipped in M1-E2, so each has to be deleted, restated as what IS "
        "true, or (if it is really about a part that is still out) repointed at the "
        f"milestone that owns it: {denials}"
    )


# --- the M1-F promise sweep -------------------------------------------------
#
# A THIRD net, over a WIDER walk (review m5): M1-F's own obligations lived in
# `src/`, `tests/`, `examples/` (the contracts that carried a TTL/rollback
# caveat) AND `docs/` (this task's own `docs/testing.md`), so this net widens
# `_WALKED` rather than reusing it -- `docs/superpowers/` stays out, for the
# same reason the two nets above leave it out: it is the PLANNING record, not
# a promise the shipped code makes.
#
# THREE needles, matched independently (a line can trip more than one):
#
# * `"sub-plan f"`, case-INSENSITIVELY, exactly like the `"sub-plan e"` needle
#   above;
# * `"tier 2b"` / `"tier-2b"`, case-INSENSITIVELY -- the sub-plan's own private
#   name for the real-host tier, which the sweep retired in favour of naming
#   the actual proof (a file, a HOST_FACTS row) instead of the jargon;
# * the possessive `"F's"` as a WHOLE WORD, case-SENSITIVELY on the capital --
#   `"F's tier 2b is the gate"` is a promise, and a lowercase "f's" (an
#   ordinary word ending in "f", possessive) is not this sub-plan at all.
#
# Every mention has to be one of IMPLEMENTED (the text now states the fact and
# cites where the proof lives: a `tests/real_host/` file, a HOST_FACTS row, a
# declared `host_diverges`), REPOINTED (M2/M3/tier 3 by name, for the
# obligations M1-F itself carried onward), REMOVED, or -- only for a genuine
# historical record the sweep chose to keep verbatim -- allowlisted below.

_WALKED_F = (_REPO_ROOT / "src", _REPO_ROOT / "tests", _REPO_ROOT / "examples", _REPO_ROOT / "docs")

#: `docs/superpowers/` is walked by `_WALKED_F` (it is under `docs/`) but
#: excluded by `_is_excluded_from_f_walk`, for the same reason as the two nets
#: above: it is the planning record, not a promise the shipped code makes.
_EXCLUDED_DOCS_PREFIX = _REPO_ROOT / "docs" / "superpowers"

_NEEDLE_SUBPLAN_F = re.compile(r"sub-plan f", re.IGNORECASE)
_NEEDLE_TIER_2B = re.compile(r"tier[- ]2b", re.IGNORECASE)
#: Case-sensitive on purpose: an ordinary possessive ending in a lowercase
#: "f's" is not this sub-plan, and the whole-word boundaries keep "M1-F's" and
#: "sub-plan F's" both caught without also catching e.g. "staff's".
_NEEDLE_FS_POSSESSIVE = re.compile(r"\bF's\b")


def _is_an_f_mention(line: str) -> bool:
    """Whether `line` trips any of the three O33 needles."""
    return bool(
        _NEEDLE_SUBPLAN_F.search(line)
        or _NEEDLE_TIER_2B.search(line)
        or _NEEDLE_FS_POSSESSIVE.search(line)
    )


#: `(relative path, exact line text stripped)` for every mention the sweep
#: audited and chose to KEEP, with why in a trailing comment -- never a live
#: promise, and never reworded away because doing so would either violate a
#: FREEZE this task does not own or would blur a specific, dated finding this
#: repo wants named exactly as it was found.
_ALLOWLIST_F: frozenset[tuple[str, str]] = frozenset(
    {
        # `tests/semantics/cases.py` is FROZEN (this task's own controller
        # ruling, mirroring D9's append-only registry): this line is a stale
        # forward promise by the letter of the net, but the file cannot be
        # reworded to fix it, so it is kept and recorded here instead.
        (
            "tests/semantics/cases.py",
            "sub-plan D/F's differential harness is exactly what must confirm or refute it",
        ),
        # A specific, dated, numbered review finding -- "M1-F's review finding
        # B1" already happened and is cited by number precisely so a reader
        # can find it again; reworded, it would stop being that citation.
        (
            "src/serpent/emitter/arith.py",
            "#: and M1-F's review finding B1 ``symsmall_cmp``.",
        ),
        (
            "tests/unit/test_emitter_symbol_compare.py",
            "Review finding **B1** of sub-plan M1-F's adversarial plan review: the shipped",
        ),
    }
)


def _mentions_f(root: Path) -> list[tuple[str, int, str]]:
    """`(relative path, 1-based line number, line text)` for every line under
    `root` tripping an O33 needle, `__pycache__` and `docs/superpowers/`
    excluded."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.resolve() == _SELF:
            continue
        resolved = path.resolve()
        if resolved == _EXCLUDED_DOCS_PREFIX or _EXCLUDED_DOCS_PREFIX in resolved.parents:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if _is_an_f_mention(line):
                found.append((str(path.relative_to(_REPO_ROOT)), lineno, line.strip()))
    return found


def _all_mentions_f() -> list[tuple[str, int, str]]:
    mentions: list[tuple[str, int, str]] = []
    for root in _WALKED_F:
        mentions.extend(_mentions_f(root))
    return mentions


def _keyed_f() -> set[tuple[str, str]]:
    """Every M1-F mention as its allowlist key: `(relative path, stripped text)`."""
    return {(path, line) for path, _lineno, line in _all_mentions_f()}


def test_every_sub_plan_f_mention_is_an_allowlisted_historical_record() -> None:
    unexpected = sorted(
        (path, lineno, line)
        for path, lineno, line in _all_mentions_f()
        if (path, line) not in _ALLOWLIST_F
    )
    assert not unexpected, (
        'unexpected M1-F mention(s) ("sub-plan F", "tier 2b"/"tier-2b", or the '
        'possessive "F\'s") outside the allowlist -- each one has to be '
        "IMPLEMENTED (state the fact and cite the real-host proof), REPOINTED "
        "(M2/M3/tier 3 by name), REMOVED, or (only if it is a genuine historical "
        f"record) added to _ALLOWLIST_F with why: {unexpected}"
    )


def test_the_f_allowlist_names_no_line_that_has_moved_or_changed() -> None:
    """The other direction, mirroring the sub-plan E net above: an allowlisted
    line that lost its needle text -- reworded, or deleted -- would silently
    stop exempting anything and ALSO stop being verified, which is a bug in
    this test rather than a pass."""
    missing = sorted(set(_ALLOWLIST_F) - _keyed_f())
    assert not missing, (
        "allowlisted M1-F line(s) no longer appear with their needle text -- the "
        "sentence was reworded or removed; re-audit it and update _ALLOWLIST_F: "
        f"{missing}"
    )


def test_the_f_net_has_teeth() -> None:
    """Both directions, pinned as samples, exactly like the surface gate above:
    a forward-looking sentence naming sub-plan F must still trip the net, and
    an honestly repointed one -- citing the real host directly, by file or by
    HOST_FACTS row, and naming no sub-plan -- must not."""
    forward_looking = (
        "the mini host cannot decode a returned union; sub-plan F's tier 2b "
        "is where it eventually gets proven against a real host."
    )
    repointed = (
        "proven on the real host: tests/real_host/test_host_facts_real.py's "
        "i128_floordiv_truncates_toward_zero row."
    )
    assert _is_an_f_mention(forward_looking)
    assert not _is_an_f_mention(repointed)
