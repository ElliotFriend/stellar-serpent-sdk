"""The real host's failures, as exceptions with TWO levels of classification.

Review B5 is the whole reason this module exists. `serpent_host.HostFailure`
carries the FRAME-level error the sdk's `try_invoke_contract` hands back, and
measured on this host that error is `Error(Context, InvalidAction)` -- type
"Context", code 6 -- for *every* guest-side failure except a contract's own
`fail_with_error`. A missing export, a refused `require_auth`, an arithmetic
overflow and a storage limit all report the same three words at that level. So:

* `.error_type`/`.code` answer only the P4 question, "was this the contract's
  own error or the host's". That is genuinely all they can answer, and pretending
  otherwise is how a test comes to assert `code == 6` and believe it measured
  something;
* `.underlying` is the innermost `Error(Type(Code))` the host wrote into its
  DIAGNOSTIC buffer, which is where the real classification survives. Every
  host-fact assertion in the real-host tier reads that.

**The rendering of `.underlying` is `(type_name, code_name)` in the Rust
spelling** -- `("Auth", "InvalidAction")`, `("Object", "ArithDomain")`,
`("WasmVm", "MissingValue")` -- not the XDR enum's own
`("SCE_AUTH", "SCEC_INVALID_ACTION")`. Two reasons to pick this end of the
choice and pin it here: it is the spelling `HostFailure.args[1]` already uses
for the frame-level type (the Rust layer reports `ScErrorType::WasmVm` by its
variant name), so the two levels are comparable without a translation table; and
it is the spelling the soroban docs and the `Error(...)` panic text use, so a
diagnostic read off a failure matches what the message next to it says.

A CONTRACT-typed diagnostic error carries its code in the XDR union's
`contract_code` arm rather than as an `ScErrorCode` name, so it renders as the
decimal string -- `("Contract", "3")`. The declared attribute type stays
`tuple[str, str] | None`, one shape for every arm, so a caller never has to
branch on whether the second element is a name or a number.
"""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, Any, NoReturn

from stellar_sdk import scval
from stellar_sdk.xdr import DiagnosticEvent, SCValType

from serpent.decorators import _METADATA_ATTR
from serpent.errors import ContractError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stellar_sdk.xdr import SCVal


class RealHostError(Exception):
    """Any failure the real host reported, with both levels of classification.

    `error_type`/`code` are the frame-level pair; `underlying` is the innermost
    error diagnostic as `(type_name, code_name_or_decimal)`, or `None` when the
    host emitted no error diagnostic at all (a contained panic outside an
    invocation, for instance, where the buffer holds no `error`-topic event).
    """

    def __init__(
        self,
        error_type: str,
        code: int,
        message: str,
        underlying: tuple[str, str] | None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.code = code
        self.underlying = underlying


class RealContractError(RealHostError):
    """`error_type == "Contract"`: the contract's own `fail_with_error` code.

    `member` is the deployed class's `@contracterror` member -- an exception
    CLASS, per M1-A's ruling that `errorcode(N)` generates one -- when the
    deployed module declares that code, and `None` when it does not (a contract
    may fail with a code no enum in the module names). It is recovered from the
    MODULE OBJECT the façade was handed rather than from `sys.modules`, because
    a path-loaded example module is not in `sys.modules` at all (review B3).

    Typed `type[ContractError] | None`, narrower than the brief's
    `type[BaseException] | None`: `@contracterror` generates `ContractError`
    subclasses and nothing else, and the narrower type is what lets a test
    compare `member.code` against the declaration without a cast -- which is
    the comparison that matters, since a second by-path load of the same module
    yields a DISTINCT class object.
    """

    def __init__(
        self,
        error_type: str,
        code: int,
        message: str,
        underlying: tuple[str, str] | None,
        member: type[ContractError] | None,
    ) -> None:
        super().__init__(error_type, code, message, underlying)
        self.member = member


class HostPanic(RealHostError):
    """A panic the Rust layer contained (`kind == "panic"`, E4).

    Its own class rather than a flavour of `RealHostError`: a panic is a place
    where the sdk escalated something it could have returned, so the frame-level
    pair is `("", 0)` and the classification survives only in the message text
    and, when there is one, the diagnostic. A test that means to assert a panic
    should have to say so.
    """


class RealHostUnavailable(RuntimeError):
    """`RealEnv()` was constructed where `serpent_host` is not importable.

    A `RuntimeError`, not a skip: the marker (`_marker`, ruling U2) is what
    decides whether a missing extension skips or fails, and it decides that
    during COLLECTION. Anything reaching a `RealEnv()` call has already been
    let through by that policy, so the honest answer here is an error naming the
    rebuild command.
    """


class FrozenTableDisagreement(AssertionError):
    """A real-leg answer disagrees with tier 1 on a FROZEN-table row (E10).

    A frozen table is a recorded decision, so a disagreement is not a bug to
    fix in passing: the message must name the row, both answers, and
    "controller decision required", so that whoever hits it escalates rather
    than editing the table to match the host.
    """


def raise_from_failure(
    exc: BaseException,
    module: ModuleType | None,
    diagnostics: Sequence[DiagnosticEvent],
) -> NoReturn:
    """Re-raise a `serpent_host.HostFailure` as the typed exception it means.

    `exc.args` is `(kind, error_type, code, message)` (`host/serpent_host.pyi`).
    The `kind` is the discrimination the Rust layer already made, and it is the
    only thing that can make it: `Error(Context, InvalidAction)` has code 6 and
    so does a contract that failed with 6, and only the layer that saw which
    arm of `InvokeError` produced it can tell them apart (P4).

    `invalid_input` and `conversion` become `ValueError`, not a `RealHostError`
    subclass: they are CALLER bugs -- a malformed strkey, an unrepresentable
    argument -- and never an answer the host gave about a contract, so they must
    not be catchable as one.

    Every kind is matched EXPLICITLY and an unrecognised one raises a loud
    `RuntimeError` naming it. The tempting default -- fold the leftovers into
    the `ValueError` arm -- would mean that the day the Rust layer grows a sixth
    kind, this façade silently reports it as a caller bug: a new host answer,
    mislabelled as the test's own mistake, is the one failure mode that would
    not show up as a test failure anywhere.
    """
    kind, error_type, code, message = (
        str(exc.args[0]),
        str(exc.args[1]),
        int(exc.args[2]),
        str(exc.args[3]),
    )
    underlying = _innermost_error(diagnostics)
    if kind == "contract":
        raise RealContractError(
            error_type, code, message, underlying, _member_for(module, code)
        ) from exc
    if kind == "host":
        raise RealHostError(error_type, code, message, underlying) from exc
    if kind == "panic":
        raise HostPanic(error_type, code, message, underlying) from exc
    if kind in ("invalid_input", "conversion"):
        raise ValueError(f"serpent_host rejected the call ({kind}): {message}") from exc
    raise RuntimeError(
        f"serpent_host reported an unknown failure kind {kind!r} ({message}). "
        "host/serpent_host.pyi documents the five kinds serpent.testing maps; "
        "a sixth means the Rust layer and this façade have drifted."
    ) from exc


def _innermost_error(diagnostics: Sequence[DiagnosticEvent]) -> tuple[str, str] | None:
    """The LAST `[Symbol("error"), Error(...)]` diagnostic, as name pair.

    The buffer is one invocation's, in chronological order (the `fn_call` event
    first), and the host writes the error events outward as the failure
    propagates -- so the LAST one is the innermost cause, which is the opposite
    of what "innermost" usually suggests about a list and worth stating.
    """
    found: tuple[str, str] | None = None
    for event in diagnostics:
        for topic in _error_topics(event):
            error = topic.error
            if error is None:
                continue
            type_name = _rust_name(error.type.name)
            if error.code is not None:
                found = (type_name, _rust_name(error.code.name))
            elif error.contract_code is not None:
                # A Contract-typed error's number lives in its own union arm and
                # has no ScErrorCode name; the decimal keeps one tuple shape.
                found = (type_name, str(error.contract_code.uint32))
    return found


def _error_topics(event: DiagnosticEvent) -> list[SCVal]:
    """The `Error(...)` topics of an `error`-topic diagnostic; `[]` otherwise.

    `body.v0` is Optional in the generated XDR, and `topics[0]` is a Symbol only
    for the events the host itself writes, so both are checked rather than
    asserted: a diagnostic buffer is host output, not our own data structure.
    """
    v0 = event.event.body.v0
    if v0 is None or not v0.topics:
        return []
    first = v0.topics[0]
    if first.type is not SCValType.SCV_SYMBOL or scval.from_symbol(first) != "error":
        return []
    return [t for t in v0.topics[1:] if t.type is SCValType.SCV_ERROR]


def _rust_name(xdr_name: str) -> str:
    """`SCE_WASM_VM` -> `WasmVm`, `SCEC_ARITH_DOMAIN` -> `ArithDomain`.

    The XDR generator spells the enum variants in SCREAMING_SNAKE with the
    union's prefix; the Rust `ScErrorType`/`ScErrorCode` variants -- and the
    `Error(...)` text in every host message -- are the CamelCase of the same
    words. This is the one place the two spellings meet.
    """
    body = xdr_name.removeprefix("SCEC_").removeprefix("SCE_")
    return "".join(word.capitalize() for word in body.split("_"))


def _member_for(module: ModuleType | None, code: int) -> type[ContractError] | None:
    """The `@contracterror` member declaring `code`, in `module`.

    Scans the module OBJECT (review B3: a path-loaded contract module has no
    `sys.modules` entry to look it up in) for `@contracterror` classes and reads
    their recorded `cases` rather than their attributes, so the answer comes
    from the same metadata the compiler and the spec emitter read.
    """
    if module is None:
        return None
    for value in vars(module).values():
        if not isinstance(value, type):
            continue
        metadata: Any = vars(value).get(_METADATA_ATTR)
        if not isinstance(metadata, dict) or metadata.get("kind") != "error_enum":
            continue
        for name, declared in metadata["cases"]:
            if declared == code:
                member = getattr(value, name)
                if isinstance(member, type) and issubclass(member, ContractError):
                    return member
    return None
