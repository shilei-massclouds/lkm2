"""Frozen data model for the version-two Model IR."""

from __future__ import annotations

from dataclasses import dataclass
import re


SCHEMA_VERSION = 2
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
        if len(self.spec) != 1:
            raise ModelIRValidationError(
                "entry.spec must contain exactly one root module identifier"
            )


@dataclass(frozen=True, slots=True)
class ModelModule:
    name: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "module.name")


@dataclass(frozen=True, slots=True)
class ModelIR:
    schema_version: int
    entry: ModelEntry
    modules: tuple[ModelModule, ...]

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
        if type(self.modules) is not tuple:
            raise ModelIRValidationError(
                "modules must be a tuple of ModelModule values"
            )
        for index, module in enumerate(self.modules):
            if not isinstance(module, ModelModule):
                raise ModelIRValidationError(
                    f"modules[{index}] must be a ModelModule"
                )

        ordered = tuple(sorted(self.modules, key=lambda module: module.name))
        names = tuple(module.name for module in ordered)
        if len(set(names)) != len(names):
            duplicate = next(
                name
                for index, name in enumerate(names[1:], 1)
                if name == names[index - 1]
            )
            raise ModelIRValidationError(
                f"duplicate module {'.'.join(duplicate)!r}"
            )
        if not names:
            raise ModelIRValidationError("modules must not be empty")

        name_set = set(names)
        for name in names:
            if len(name) > 1 and name[:-1] not in name_set:
                raise ModelIRValidationError(
                    f"module {'.'.join(name)!r} is missing parent module "
                    f"{'.'.join(name[:-1])!r}"
                )

        roots = {name for name in names if len(name) == 1}
        expected_roots = {self.entry.spec}
        if roots != expected_roots:
            raise ModelIRValidationError(
                "root modules must exactly match entry.spec"
            )

        origin_module = self.entry.origin[:-1]
        if origin_module not in name_set:
            rendered = ".".join(origin_module) or "<crate>"
            raise ModelIRValidationError(
                f"entry.origin module prefix {rendered!r} does not exist"
            )

        object.__setattr__(self, "modules", ordered)
