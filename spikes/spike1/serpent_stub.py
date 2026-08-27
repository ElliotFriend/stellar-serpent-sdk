"""Typed no-op stand-ins for the serpent contract-authoring surface (Spike 1).

These definitions exist only so ``spikes/spike1/contract_src.py`` can be
compiled (``python -m py_compile``) and analyzed with ``mypy --strict``.
Nothing here has runtime behavior and nothing here is part of the real
serpent authoring API -- this is throwaway spike code (see spikes/README.md).
"""

from __future__ import annotations

import dataclasses
from typing import Any, TypeVar, dataclass_transform

T = TypeVar("T")


class U32:
    """Stand-in for a Soroban u32 value."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __add__(self, other: U32) -> U32:
        return U32(self.value + other.value)

    def __gt__(self, other: U32) -> bool:
        return self.value > other.value


class Symbol:
    """Stand-in for a Soroban Symbol value."""

    def __init__(self, value: str) -> None:
        self.value = value


class String:
    """Stand-in for a Soroban String value."""

    def __init__(self, value: str) -> None:
        self.value = value


class _StorageBucket:
    """Stand-in for one of env.storage().{instance,persistent,temporary}()."""

    def get(self, key: Symbol, type_: type[T], *, default: T | None = None) -> T:
        raise NotImplementedError

    def set(self, key: Symbol, value: Any) -> None:
        raise NotImplementedError


class Storage:
    """Stand-in for env.storage()."""

    def instance(self) -> _StorageBucket:
        raise NotImplementedError

    def persistent(self) -> _StorageBucket:
        raise NotImplementedError

    def temporary(self) -> _StorageBucket:
        raise NotImplementedError


class Env:
    """Stand-in for the Soroban host environment handle."""

    def storage(self) -> Storage:
        raise NotImplementedError


def contract(cls: type[T]) -> type[T]:
    """No-op class decorator marking a contract's export surface."""
    return cls


@dataclass_transform()
def contracttype(cls: type[T]) -> type[T]:
    """Class decorator for Soroban struct/union types.

    Backed by ``dataclasses.dataclass`` so that, via ``dataclass_transform``,
    mypy understands kwargs construction such as
    ``Settings(counter_limit=..., display_name=...)``.
    """
    return dataclasses.dataclass(cls)


def contracterror(cls: type[T]) -> type[T]:
    """No-op class decorator marking a contract's error enum."""
    return cls
