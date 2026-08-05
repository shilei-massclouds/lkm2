"""Frozen data model for the version-one Model IR."""

from __future__ import annotations

from dataclasses import dataclass
import re


SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ModelIRValidationError(ValueError):
    """Raised when an in-memory or serialized Model IR is invalid."""


def _validate_qualified_name(value: object, path: str) -> None:
    if type(value) is not tuple:
        raise ModelIRValidationError(f"{path} must be a tuple of identifiers")
    if not value:
        raise ModelIRValidationError(f"{path} must not be empty")
    for index, part in enumerate(value):
        if type(part) is not str:
            raise ModelIRValidationError(f"{path}[{index}] must be a string")
        if _IDENTIFIER.fullmatch(part) is None:
            raise ModelIRValidationError(
                f"{path}[{index}] is not a valid identifier: {part!r}"
            )


@dataclass(frozen=True, slots=True)
class ModelEntry:
    origin: tuple[str, ...]
    spec: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_qualified_name(self.origin, "entry.origin")
        _validate_qualified_name(self.spec, "entry.spec")


@dataclass(frozen=True, slots=True)
class ModelIR:
    schema_version: int
    entry: ModelEntry

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise ModelIRValidationError("schema_version must be an integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelIRValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        if not isinstance(self.entry, ModelEntry):
            raise ModelIRValidationError("entry must be a ModelEntry")
