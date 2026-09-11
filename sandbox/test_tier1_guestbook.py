"""Ye Olde Guestbook at tier 1: the contract's methods run as plain Python.

`serpent.env.Env` is an in-memory MODEL of the host (storage, ledger, auth
recording); `deploy` runs `__init__` for real. No WASM is built or run here,
so a green test is evidence about the model, not the chain -- the real-host
twin of this file is `test_tier2_guestbook.py`.

One test per test in the Rust contract's `test.rs`, same names.

    uv run --no-sync pytest -q sandbox/test_tier1_guestbook.py
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from stellar_sdk.strkey import StrKey

from serpent import U32, Address, String
from serpent.env import AuthorizationFailed, ConstructorFailed, Env, deploy


def _load_guestbook() -> ModuleType:
    source = Path(__file__).with_name("guestbook.py")
    spec = importlib.util.spec_from_file_location("sandbox_guestbook", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guestbook = _load_guestbook()
Message = guestbook.Message
Error = guestbook.Error


def _address(label: str) -> Address:
    """A deterministic contract address per role (any valid strkey would do at tier 1)."""
    return Address(StrKey.encode_contract(hashlib.sha256(label.encode()).digest()))


ADMIN = _address("admin")
AUTHOR = _address("author")
OUTSIDER = _address("outsider")

HELLO_WORLD = String("Hello World")
LOREM_IPSUM = String("Lorem Ipsum ain't got nothin' on me!")
EMPTY = String("")


def new_guestbook() -> tuple[Env, Any]:
    """A fresh Env whose allow-set names the admin and one author, with the
    guestbook deployed (the constructor writes message 1, the welcome)."""
    env = Env(auths=(ADMIN, AUTHOR))
    contract = deploy(guestbook.GuestbookContract, env, ADMIN, HELLO_WORLD, LOREM_IPSUM)
    return env, contract


# --- constructor -------------------------------------------------------------------


def test_constructor() -> None:
    env, contract = new_guestbook()
    with env.frame():
        welcome = contract.read_message(env, U32(1))
    assert welcome.author == ADMIN
    assert welcome.title == HELLO_WORLD
    assert welcome.text == LOREM_IPSUM


def test_constructor_auth() -> None:
    """The constructor called `admin.require_auth()`: the model recorded it."""
    env, _contract = new_guestbook()
    assert env.recorded_auths == ((ADMIN, None),)


def test_constructor_empty_title() -> None:
    # A constructor's own error surfaces as ConstructorFailed (the host launders
    # constructor errors, so a deployer never sees the code); the member's name
    # is in the message.
    with pytest.raises(ConstructorFailed, match="InvalidMessage"):
        deploy(guestbook.GuestbookContract, Env(auths=(ADMIN,)), ADMIN, EMPTY, LOREM_IPSUM)


def test_constructor_empty_text() -> None:
    with pytest.raises(ConstructorFailed, match="InvalidMessage"):
        deploy(guestbook.GuestbookContract, Env(auths=(ADMIN,)), ADMIN, HELLO_WORLD, EMPTY)


# --- write_message -----------------------------------------------------------------


def test_write_message() -> None:
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    assert message_id == U32(2)  # the welcome is message 1


def test_write_message_auth() -> None:
    env, contract = new_guestbook()
    with env.frame():
        contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
    # the constructor's auth, then this call's
    assert env.recorded_auths == ((ADMIN, None), (AUTHOR, None))


def test_write_message_unauthorized() -> None:
    """No Rust twin (mock_all_auths hides it there): an address the allow-set
    does not name is a host TRAP, not a contract error."""
    env, contract = new_guestbook()
    with env.frame(), pytest.raises(AuthorizationFailed):
        contract.write_message(env, OUTSIDER, HELLO_WORLD, LOREM_IPSUM)


def test_write_message_empty_title() -> None:
    env, contract = new_guestbook()
    with env.frame(), pytest.raises(Error.InvalidMessage):
        contract.write_message(env, AUTHOR, EMPTY, LOREM_IPSUM)


def test_write_message_empty_text() -> None:
    env, contract = new_guestbook()
    with env.frame(), pytest.raises(Error.InvalidMessage):
        contract.write_message(env, AUTHOR, HELLO_WORLD, EMPTY)


# --- read_message / read_latest -----------------------------------------------------


def test_read_message() -> None:
    env, contract = new_guestbook()
    with env.frame():
        contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        first = contract.read_message(env, U32(1))
        second = contract.read_message(env, U32(2))
        ledger = env.ledger().sequence()
    assert first == Message(author=ADMIN, ledger=ledger, title=HELLO_WORLD, text=LOREM_IPSUM)
    assert second == Message(author=AUTHOR, ledger=ledger, title=HELLO_WORLD, text=LOREM_IPSUM)


def test_read_message_non_existent_id() -> None:
    env, contract = new_guestbook()
    with env.frame(), pytest.raises(Error.NoSuchMessage):
        contract.read_message(env, U32(3))


def test_read_latest() -> None:
    env, contract = new_guestbook()
    diff_title = String("A Different Title")
    diff_text = String("A completely distinct text.")
    with env.frame():
        contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        contract.write_message(env, AUTHOR, diff_title, diff_text)
        latest = contract.read_latest(env)
    assert latest.author == AUTHOR
    assert latest.title == diff_title
    assert latest.text == diff_text


# --- edit_message ------------------------------------------------------------------

NEW_TITLE = String("Updated Hello World")
NEW_TEXT = String("Lorem Ipsum STILL ain't got nothin' on me!")


def test_edit_message() -> None:
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        contract.edit_message(env, message_id, NEW_TITLE, NEW_TEXT)
        edited = contract.read_message(env, message_id)
    assert edited.title == NEW_TITLE
    assert edited.text == NEW_TEXT


def test_edit_message_auth() -> None:
    """`edit_message` requires the ORIGINAL author's auth (read back from storage)."""
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        contract.edit_message(env, message_id, NEW_TITLE, NEW_TEXT)
    assert env.recorded_auths == ((ADMIN, None), (AUTHOR, None), (AUTHOR, None))


def test_edit_message_bad_message_id() -> None:
    env, contract = new_guestbook()
    with env.frame(), pytest.raises(Error.NoSuchMessage):
        contract.edit_message(env, U32(99), NEW_TITLE, NEW_TEXT)


def test_edit_message_empty_title() -> None:
    """An empty title keeps the old title; only the text changes."""
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        contract.edit_message(env, message_id, EMPTY, NEW_TEXT)
        edited = contract.read_message(env, message_id)
    assert edited.title == HELLO_WORLD
    assert edited.text == NEW_TEXT


def test_edit_message_empty_text() -> None:
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        contract.edit_message(env, message_id, NEW_TITLE, EMPTY)
        edited = contract.read_message(env, message_id)
    assert edited.title == NEW_TITLE
    assert edited.text == LOREM_IPSUM


def test_edit_message_empty_title_and_text() -> None:
    env, contract = new_guestbook()
    with env.frame():
        message_id = contract.write_message(env, AUTHOR, HELLO_WORLD, LOREM_IPSUM)
        with pytest.raises(Error.InvalidMessage):
            contract.edit_message(env, message_id, EMPTY, EMPTY)
