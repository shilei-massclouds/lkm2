"""Rust-style module discovery and shallow ``use`` resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ast import ModelSpec
from .diagnostics import error
from .module_parser import Location, UseDeclaration, parse_module


@dataclass(frozen=True, slots=True)
class _LoadedModule:
    name: tuple[str, ...]
    path: Path
    uses: tuple[UseDeclaration, ...]


class _ModuleGraphLoader:
    def __init__(self, entry_path: Path) -> None:
        self.entry_path = entry_path
        self.modules: dict[tuple[str, ...], _LoadedModule] = {}
        self.load_order: list[_LoadedModule] = []
        self.physical_files: dict[tuple[int, int], tuple[str, ...]] = {}

    def load(self, document: ModelSpec) -> tuple[tuple[str, ...], ...]:
        root_name = document.spec.name.parts
        root_path = self.entry_path.parent / f"{root_name[0]}.spec"
        span = document.spec.span
        self._load_module(
            root_name,
            root_path,
            self.entry_path,
            Location(span.start_line, span.start_column),
        )
        self._resolve_uses()
        return tuple(sorted(self.modules))

    def _load_module(
        self,
        name: tuple[str, ...],
        path: Path,
        declaring_path: Path,
        declaration_location: Location,
    ) -> None:
        try:
            file_stat = path.stat()
        except FileNotFoundError as exc:
            raise error(
                declaring_path,
                declaration_location.line,
                declaration_location.column,
                f"module {'.'.join(name)!r} not found; expected {path}",
            ) from exc
        except OSError as exc:
            raise error(path, 1, 1, exc.strerror or str(exc)) from exc

        identity = (file_stat.st_dev, file_stat.st_ino)
        previous_name = self.physical_files.get(identity)
        if previous_name is not None:
            raise error(
                declaring_path,
                declaration_location.line,
                declaration_location.column,
                f"module file {path} is already loaded as {'.'.join(previous_name)!r}",
            )

        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise error(path, 1, 1, "source is not valid UTF-8") from exc
        except OSError as exc:
            raise error(path, 1, 1, exc.strerror or str(exc)) from exc

        parsed = parse_module(source, path)
        loaded = _LoadedModule(name, path, parsed.uses)
        self.physical_files[identity] = name
        self.modules[name] = loaded
        self.load_order.append(loaded)

        child_directory = path.with_suffix("")
        for declaration in parsed.declarations:
            child_name = name + (declaration.name,)
            child_path = child_directory / f"{declaration.name}.spec"
            self._load_module(
                child_name,
                child_path,
                path,
                declaration.location,
            )

    def _resolve_uses(self) -> None:
        module_names = set(self.modules)
        for module in self.load_order:
            imported_names: set[str] = set()
            for declaration in module.uses:
                target, local_name = self._resolve_use(module, declaration)
                module_prefix = target[:-1]
                if module_prefix and module_prefix not in module_names:
                    location = declaration.location
                    raise error(
                        module.path,
                        location.line,
                        location.column,
                        f"cannot resolve module {'.'.join(module_prefix)!r} "
                        "in use path",
                    )
                if local_name in imported_names:
                    location = declaration.locations[-1]
                    raise error(
                        module.path,
                        location.line,
                        location.column,
                        f"duplicate local import name {local_name!r}",
                    )
                imported_names.add(local_name)

    def _resolve_use(
        self, module: _LoadedModule, declaration: UseDeclaration
    ) -> tuple[tuple[str, ...], str]:
        parts = declaration.parts
        first = parts[0]
        cursor = 0

        if first == "crate":
            base: tuple[str, ...] = ()
            cursor = 1
        elif first == "self":
            base = module.name
            cursor = 1
        elif first == "super":
            while cursor < len(parts) and parts[cursor] == "super":
                cursor += 1
            if cursor > len(module.name):
                location = declaration.locations[len(module.name)]
                raise error(
                    module.path,
                    location.line,
                    location.column,
                    "use path has too many leading 'super' components",
                )
            base = module.name[: len(module.name) - cursor]
        else:
            base = ()

        if cursor == len(parts):
            location = declaration.locations[-1]
            raise error(
                module.path,
                location.line,
                location.column,
                f"use path cannot end with {parts[-1]!r}",
            )

        for index in range(cursor, len(parts)):
            if parts[index] in {"crate", "self", "super"}:
                location = declaration.locations[index]
                raise error(
                    module.path,
                    location.line,
                    location.column,
                    f"{parts[index]!r} is only allowed at the start of a use path",
                )

        remainder = parts[cursor:]
        return base + remainder, remainder[-1]


def load_module_graph(
    entry_path: Path, document: ModelSpec
) -> tuple[tuple[str, ...], ...]:
    """Load all explicitly declared modules and validate their simple imports."""

    return _ModuleGraphLoader(entry_path).load(document)
