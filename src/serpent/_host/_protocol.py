"""The computed protocol floor and gate-check logic.

A peer of `__init__.py`, not a dependent of it -- like `__init__.py`, this
module builds its own name -> `HostFn` index straight from `_model` and
`bindings`, so `__init__.py` staying "re-exports only" (per its own
docstring) never creates an import cycle here.
"""

from collections.abc import Iterable

from serpent._host._model import index_functions_by_name
from serpent._host.bindings import HOST_FUNCTIONS

#: The gate-check ceiling `declared_protocol` uses when `requested` is
#: omitted. NOT what an ungated contract declares -- see `declared_protocol`.
DEFAULT_TARGET_PROTOCOL = 27

#: The protocol floor below which no pinned host function requires anything
#: higher -- `compute_protocol_floor` never returns less than this.
BASE_PROTOCOL = 20

_FUNCTIONS_BY_NAME = index_functions_by_name(HOST_FUNCTIONS)


class ProtocolGateError(ValueError):
    """>=1 named host function cannot run at the checked protocol target.

    The message names every offending function and the min_protocol or
    max_protocol that rules it out.
    """


def compute_protocol_floor(fn_names: Iterable[str]) -> int:
    """The lowest protocol at which every named function can run.

    `max(BASE_PROTOCOL, *min_protocol of the named fns)` -- a fn without a
    declared `min_protocol` contributes `BASE_PROTOCOL`, not 0. An unknown
    name raises `KeyError` naming it (bindings are looked up BY NAME).
    """
    floor = BASE_PROTOCOL
    for name in fn_names:
        min_protocol = _FUNCTIONS_BY_NAME[name].min_protocol
        if min_protocol is not None and min_protocol > floor:
            floor = min_protocol
    return floor


def check_protocol_target(fn_names: Iterable[str], target: int) -> None:
    """Raise `ProtocolGateError` naming every named fn incompatible with `target`.

    A fn is incompatible if its `min_protocol` exceeds `target`, or its
    `max_protocol` is below `target`. An unknown name raises `KeyError`
    naming it. `fn_names` is deduped (first-seen order preserved) before
    checking, so a duplicate input name is never named twice in the message.
    """
    seen: dict[str, None] = dict.fromkeys(fn_names)
    offenders: list[str] = []
    for name in seen:
        fn = _FUNCTIONS_BY_NAME[name]
        if fn.min_protocol is not None and fn.min_protocol > target:
            offenders.append(f"{fn.name} (min_protocol={fn.min_protocol} > target={target})")
        if fn.max_protocol is not None and fn.max_protocol < target:
            offenders.append(f"{fn.name} (max_protocol={fn.max_protocol} < target={target})")
    if offenders:
        raise ProtocolGateError(
            f"host functions incompatible with protocol target {target}: " + ", ".join(offenders)
        )


def declared_protocol(fn_names: Iterable[str], requested: int | None) -> int:
    """The protocol value `build_env_meta` should be called with.

    Per spec Sec.4: with `requested is None` (an `is None` check, never
    truthiness), this is `compute_protocol_floor(fn_names)` -- NOT
    `DEFAULT_TARGET_PROTOCOL`, which is only the gate-check ceiling used in
    that case. `check_protocol_target` always runs first, against
    `requested` if given, else `DEFAULT_TARGET_PROTOCOL`; an explicit
    `requested` below the computed floor raises `ValueError` (never
    silently raised to the floor). An unknown name raises `KeyError` naming
    it, from the underlying lookup.
    """
    names = list(fn_names)
    check_protocol_target(names, requested if requested is not None else DEFAULT_TARGET_PROTOCOL)
    floor = compute_protocol_floor(names)
    if requested is None:
        return floor
    if requested < floor:
        raise ValueError(f"requested protocol {requested} is below the computed floor {floor}")
    return requested
