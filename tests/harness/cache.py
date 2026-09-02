"""The compiled-module cache: tier-2a productization item (i) (ruling E7, O1).

`build_file` is a real compile -- lexer through `wasm-tools validate` -- and
the differential tables (`tests/unit/test_env_differential.py`'s ~60-row
`ENV_SCENARIOS`, `tests/unit/test_emitter_end_to_end.py`'s per-fixture
properties) replay the SAME handful of fixtures over and over. Compiling one of
them sixty times to answer sixty different questions about its already-fixed
bytes was the wall-time cost this module removes.

**What is cached, and what is not (C7's rule).** Only the `BuildResult` --
the wasm bytes and the facts `AssembledModule` carried through
(`serpent.emitter.BuildResult`) -- is ever memoised. The HOST is never
cached: every test that runs a cached build still gets its own fresh
`FullHost`/`MiniHost`, because a shared store would make one test's writes
another test's setup, which is exactly the isolation bug a cache must not
introduce.

**The cache key is content, not mtime.** `(resolved path, sha256 of the file
text)` rather than `(path, mtime)` or `(path,)` alone: a `tmp_path` fixture
frequently reuses a byte-identical path across test runs with a filesystem
clock too coarse to tell two rapid writes apart, and a path-only key would go
stale the moment a caller (deliberately, as `test_harness_objects.py` does for
its own `built()` test) edits a fixture in place. Hashing the text answers
both correctly: the same path with the same text is one compile per session,
and a changed path rebuilds unconditionally.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from serpent.emitter import BuildResult, build_file

__all__ = ["built"]

_CACHE: dict[tuple[Path, str], BuildResult] = {}


def built(path: Path) -> BuildResult:
    """`build_file(path)` memoised on `(resolved path, sha256 of the file text)`.

    A changed source rebuilds; an unchanged one is compiled once per session.
    The HOST is never cached (C7's rule) -- only the bytes.
    """
    resolved = path.resolve()
    digest = hashlib.sha256(resolved.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    key = (resolved, digest)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = build_file(resolved)
    _CACHE[key] = result
    return result
