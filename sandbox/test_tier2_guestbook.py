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

## EXPECTED TO FAIL today, on purpose

`RealEnv` deploys through the sdk test host's `register`, which runs the
constructor as a SUB-invocation of the CreateContractV2 host function under
recording auth with non-root authorization DISABLED. This constructor calls
`admin.require_auth()`, so every deploy here traps with
`Error(Auth, InvalidAction)` before the contract exists. The contract is fine
(the same bytes deploy to testnet under real authorization); the gap is in
serpent's embedded host and is journaled as an M2 item
(`mock_all_auths_allowing_non_root_auth` in host/src/lib.rs). Every test in
this file that deploys successfully is `xfail(strict=True)`: green would be a
loud failure, which is the cue to delete the `constructor_auth_gap` marker.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from stellar_sdk.strkey import StrKey

from serpent import U32, Address, String
from serpent.env import DEFAULT_LEDGER_SEQUENCE
from serpent.testing import RealContract, RealContractError, RealEnv, RealHostError

pytestmark = pytest.mark.real_host

#: Every test that DEPLOYS the guestbook successfully hits the gap described
#: above, so each carries this marker; the two empty-constructor tests do not
#: (their deploy fails on the contract's own check, before `require_auth`).
constructor_auth_gap = pytest.mark.xfail(
    strict=True,
    raises=RealHostError,
    reason="the embedded host cannot yet deploy a constructor that calls require_auth (M2)",
)

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
    run (message 1 is the welcome)."""
    env = RealEnv(auths=(ADMIN, AUTHOR))
    contract = env.deploy_source(SOURCE, ADMIN, HELLO_WORLD, LOREM_IPSUM)
    return env, contract


def _code(exc: pytest.ExceptionInfo[RealContractError]) -> int:
    return exc.value.code


# --- constructor -------------------------------------------------------------------


@constructor_auth_gap
def test_constructor() -> None:
    _env, contract = new_guestbook()
    welcome = contract.invoke("read_message", U32(1))
    assert welcome == Message(author=ADMIN, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)


@constructor_auth_gap
def test_constructor_auth() -> None:
    """The host recorded the admin's authorization of the constructor call."""
    _env, contract = new_guestbook()
    assert contract.auths() == ((ADMIN, None),)


def test_constructor_empty_title() -> None:
    # The host launders a constructor's error into a frame-level failure; the
    # contract code is only in the diagnostics. What a deployer sees is the trap.
    env = RealEnv(auths=(ADMIN,))
    with pytest.raises(RealHostError):
        env.deploy_source(SOURCE, ADMIN, EMPTY, LOREM_IPSUM)


def test_constructor_empty_text() -> None:
    env = RealEnv(auths=(ADMIN,))
    with pytest.raises(RealHostError):
        env.deploy_source(SOURCE, ADMIN, HELLO_WORLD, EMPTY)


# --- write_message -----------------------------------------------------------------


@constructor_auth_gap
def test_write_message() -> None:
    _env, contract = new_guestbook()
    assert contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM) == U32(2)


@constructor_auth_gap
def test_write_message_auth() -> None:
    _env, contract = new_guestbook()
    contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    assert contract.auths() == ((AUTHOR, None),)  # this call's auth


@constructor_auth_gap
def test_write_message_unauthorized() -> None:
    """An address the allow-set does not name: a host trap, `Auth` class."""
    _env, contract = new_guestbook()
    with pytest.raises(RealHostError) as exc:
        contract.invoke("write_message", OUTSIDER, HELLO_WORLD, LOREM_IPSUM)
    assert not isinstance(exc.value, RealContractError)
    assert exc.value.underlying is not None and exc.value.underlying[0] == "Auth"


@constructor_auth_gap
def test_write_message_empty_title() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("write_message", AUTHOR, EMPTY, LOREM_IPSUM)
    assert _code(exc) == 1  # InvalidMessage


@constructor_auth_gap
def test_write_message_empty_text() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("write_message", AUTHOR, HELLO_WORLD, EMPTY)
    assert _code(exc) == 1  # InvalidMessage


# --- read_message / read_latest -----------------------------------------------------


@constructor_auth_gap
def test_read_message() -> None:
    _env, contract = new_guestbook()
    contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    first = contract.invoke("read_message", U32(1))
    second = contract.invoke("read_message", U32(2))
    assert first == Message(author=ADMIN, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)
    assert second == Message(author=AUTHOR, ledger=LEDGER, title=HELLO_WORLD, text=LOREM_IPSUM)


@constructor_auth_gap
def test_read_message_non_existent_id() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("read_message", U32(3))
    assert _code(exc) == 2  # NoSuchMessage


@constructor_auth_gap
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


@constructor_auth_gap
def test_edit_message() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, NEW_TEXT)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=NEW_TITLE, text=NEW_TEXT)


@constructor_auth_gap
def test_edit_message_auth() -> None:
    """`edit_message` requires the ORIGINAL author's auth (read back from storage)."""
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, NEW_TEXT)
    assert contract.auths() == ((AUTHOR, None),)  # the edit's auth (auths() is per call)


@constructor_auth_gap
def test_edit_message_bad_message_id() -> None:
    _env, contract = new_guestbook()
    with pytest.raises(RealContractError) as exc:
        contract.invoke("edit_message", U32(99), NEW_TITLE, NEW_TEXT)
    assert _code(exc) == 2  # NoSuchMessage


@constructor_auth_gap
def test_edit_message_empty_title() -> None:
    """An empty title keeps the old title; only the text changes."""
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, EMPTY, NEW_TEXT)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=HELLO_WORLD, text=NEW_TEXT)


@constructor_auth_gap
def test_edit_message_empty_text() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    contract.invoke("edit_message", message_id, NEW_TITLE, EMPTY)
    edited = contract.invoke("read_message", message_id)
    assert edited == Message(author=AUTHOR, ledger=LEDGER, title=NEW_TITLE, text=LOREM_IPSUM)


@constructor_auth_gap
def test_edit_message_empty_title_and_text() -> None:
    _env, contract = new_guestbook()
    message_id = contract.invoke("write_message", AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    with pytest.raises(RealContractError) as exc:
        contract.invoke("edit_message", message_id, EMPTY, EMPTY)
    assert _code(exc) == 1  # InvalidMessage
