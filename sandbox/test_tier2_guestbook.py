"""Ye Olde Guestbook on the real host: the same source built to WASM and run by
the embedded `soroban-env-host` (`serpent.testing.RealEnv`).

This is the first tier that is evidence about the CHAIN rather than about a
model of it. `contract.invoke(method, *args)` decodes each result as the
method's own return annotation declares (a `Message` comes back as a
`Message`), a contract error arrives as `RealContractError` with the member's
`.code`, and a host refusal (an unauthorized address) as a `RealHostError`.

One test per test in the Rust contract's `test.rs`, same names; the tier-1
twin of this file is `test_tier1_guestbook.py`.

    uv run --no-sync pytest -q sandbox/test_tier2_guestbook.py
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from stellar_sdk.strkey import StrKey

from serpent import U32, Address, String, Vec
from serpent.env import DEFAULT_LEDGER_SEQUENCE
from serpent.testing import RealContract, RealContractError, RealEnv, RealHostError
from serpent.types._ordering import ChainValue

pytestmark = pytest.mark.real_host


SOURCE = Path(__file__).with_name("guestbook.py")


def _load_guestbook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sandbox_guestbook", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guestbook = _load_guestbook()
Message = guestbook.Message


def _address(label: str) -> Address:
    """A deterministic CONTRACT strkey per role: the real host mocks an allowed
    address by registering a stand-in contract at it, so account (G...)
    authorizers -- which need real signatures -- are M2."""
    return Address(StrKey.encode_contract(hashlib.sha256(label.encode()).digest()))


ADMIN = _address("admin")
AUTHOR = _address("author")
OUTSIDER = _address("outsider")

HELLO_WORLD = String("Hello World")
LOREM_IPSUM = String("Lorem Ipsum ain't got nothin' on me!")
EMPTY = String("")
#: Both legs stamp messages with the same default ledger sequence (serpent.env's
#: constant, which the embedded host is configured from).
LEDGER = U32(DEFAULT_LEDGER_SEQUENCE)


def new_guestbook() -> tuple[RealEnv, RealContract]:
    """A fresh real host whose allow-set names the admin and one author, with
    the guestbook compiled from `guestbook.py`, deployed, and its constructor
    run (message 1 is the welcome).

    `deploy_module` rather than `deploy_source`, so the `Message` values
    `invoke` decodes are instances of THIS file's `Message` (a second load of
    the same source would be a different class, and never compare equal)."""
    env = RealEnv(auths=(ADMIN, AUTHOR))
    contract = env.deploy_module(guestbook, ADMIN, HELLO_WORLD, LOREM_IPSUM)
    return env, contract


#: The element class the facade gives a mixed-kind argument list. A Protocol,
#: so mypy needs it widened before it will pass it as a `type[T]`.
_MIXED: type[Any] = ChainValue


def _auth(who: Address, *args: object) -> tuple[Address, Vec[Any]]:
    """One recorded authorization: the real host records the invocation's own
    argument list (tier 1 records `None` for a bare `require_auth()`)."""
    return (who, Vec(_MIXED, list(args)))


def _code(exc: pytest.ExceptionInfo[RealContractError]) -> int:
    return exc.value.code


# --- constructor -------------------------------------------------------------------


def test_constructor() -> None:
    _env, contract = new_guestbook()
    welcome = contract.invoke("read_message", U32(1))
    assert welcome == Message(author=ADMIN, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)


def test_constructor_auth() -> None:
    """The host recorded the admin's authorization of the constructor call."""
    _env, contract = new_guestbook()
    assert contract.auths() == (_auth(ADMIN, ADMIN, HELLO_WORLD, LOREM_IPSUM),)


def test_constructor_empty_title() -> None:
    # The host launders a constructor's error into a frame-level failure; the
    # contract code is only in the diagnostics. What a deployer sees is the trap.
    env = RealEnv(auths=(ADMIN,))
    with pytest.raises(RealHostError):
        env.deploy_module(guestbook, ADMIN, EMPTY, LOREM_IPSUM)


def test_constructor_empty_text() -> None:
    env = RealEnv(auths=(ADMIN,))
    with pytest.raises(RealHostError):
        env.deploy_module(guestbook, ADMIN, HELLO_WORLD, EMPTY)


# --- write_message -----------------------------------------------------------------


def test_write_message() -> None:
    _env, contract = new_guestbook()
    assert contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM) == U32(2)


def test_write_message_auth() -> None:
    _env, contract = new_guestbook()
    contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    assert contract.auths() == (_auth(AUTHOR, AUTHOR, HELLO_WORLD, LOREM_IPSUM),)  # this call's


def test_write_message_unauthorized() -> None:
    """An address the allow-set does not name: a host trap, `Auth` class."""
    _env, contract = new_guestbook()
    with pytest.raises(RealHostError) as exc:
        contract.invoke("write_message", OUTSIDER, HELLO_WORLD, LOREM_IPSUM)
    assert not isinstance(exc.value, RealContractError)
    assert exc.value.underlying is not None and exc.value.underlying[0] == "Auth"


def test_write_message_empty_title() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("write_message", AUTHOR, EMPTY, LOREM_IPSUM)
    assert _code(exc) == 1  # InvalidMessage


def test_write_message_empty_text() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("write_message", AUTHOR, HELLO_WORLD, EMPTY)
    assert _code(exc) == 1  # InvalidMessage


# --- read_message / read_latest -----------------------------------------------------


def test_read_message() -> None:
    _env, contract = new_guestbook()
    contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    first = contract.invoke("read_message", U32(1))
    second = contract.invoke("read_message", U32(2))
    assert first == Message(author=ADMIN, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)
    assert second == Message(author=AUTHOR, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)


def test_read_message_non_existent_id() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("read_message", U32(3))
    assert _code(exc) == 2  # NoSuchMessage


def test_read_latest() -> None:
    _env, contract = new_guestbook()
    diff_title = String("A Different Title")
    diff_text = String("A completely distinct text.")
    contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("write_message", AUTHOR, diff_title, diff_text)
    latest = contract.invoke("read_latest")
    assert latest == Message(author=AUTHOR, ledger=LEDGER, title=diff_title, text=diff_text)


# --- edit_message ------------------------------------------------------------------

NEW_TITLE = String("Updated Hello World")
NEW_TEXT = String("Lorem Ipsum STILL ain't got nothin' on me!")


def test_edit_message() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, NEW_TEXT)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=NEW_TITLE, text=NEW_TEXT)


def test_edit_message_auth() -> None:
    """`edit_message` requires the ORIGINAL author's auth (read back from storage)."""
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, NEW_TEXT)
    # The edit's auth, with the edit's args: auths() is per call.
    assert contract.auths() == (_auth(AUTHOR, message_id, NEW_TITLE, NEW_TEXT),)


def test_edit_message_bad_message_id() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("edit_message", U32(99), NEW_TITLE, NEW_TEXT)
    assert _code(exc) == 2  # NoSuchMessage


def test_edit_message_empty_title() -> None:
    """An empty title keeps the old title; only the text changes."""
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, EMPTY, NEW_TEXT)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=HELLO_WORLD, text=NEW_TEXT)


def test_edit_message_empty_text() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, EMPTY)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=NEW_TITLE, text=LOREM_IPSUM)


def test_edit_message_empty_title_and_text() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    with pytest.raises(RealContractError) as exc:
        contract.invoke("edit_message", message_id, EMPTY, EMPTY)
    assert _code(exc) == 1  # InvalidMessage
