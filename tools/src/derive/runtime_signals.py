"""Line-oriented external signal programs for inference-owned user runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TextIO


_FAMILIES = frozenset({"interrupt", "exception", "syscall"})
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")
_CPU = re.compile(r"[0-9]+\Z")
_I32_MIN = -(2**31)
_I32_MAX = 2**31 - 1


class UserRuntimeSignalValidationError(ValueError):
    """Raised when a user-runtime signal source is structurally invalid."""


@dataclass(frozen=True, slots=True)
class UserRuntimeSignal:
    family: str
    name: str
    cpu_target: int | None
    arguments: tuple[int, ...]
    source: str
    line: int
    column: int

    @property
    def local(self) -> bool:
        return self.cpu_target is None

    @property
    def qualified_name(self) -> str:
        return f"{self.family}.{self.name}"


@dataclass(frozen=True, slots=True)
class UserRuntimeSignalProgram:
    signals: tuple[UserRuntimeSignal, ...]

    def __post_init__(self) -> None:
        if type(self.signals) is not tuple or any(
            not isinstance(item, UserRuntimeSignal) for item in self.signals
        ):
            raise TypeError("signals must be a tuple of UserRuntimeSignal values")


def _location(source: str, line: int, column: int, message: str) -> str:
    return f"{source}:{line}:{column}: {message}"


def _fail(source: str, line: int, column: int, message: str) -> None:
    raise UserRuntimeSignalValidationError(
        _location(source, line, column, message)
    )


def _tokens(text: str) -> tuple[tuple[str, int], ...]:
    return tuple((match.group(), match.start() + 1) for match in re.finditer(r"\S+", text))


def load_user_runtime_signals(stream: TextIO) -> UserRuntimeSignalProgram:
    """Load and validate one line-oriented user-runtime signal program."""

    if not hasattr(stream, "read"):
        raise TypeError("stream must be a readable text stream")
    source = str(getattr(stream, "name", "<stream>"))
    result: list[UserRuntimeSignal] = []
    terminal_exit: UserRuntimeSignal | None = None
    for line_number, raw_line in enumerate(stream, 1):
        text = raw_line.split("#", 1)[0]
        items = _tokens(text)
        if not items:
            continue
        if terminal_exit is not None:
            _fail(
                source,
                line_number,
                items[0][1],
                f"signal is unreachable after syscall.exit on line {terminal_exit.line}",
            )
        if len(items) < 2:
            token, column = items[0]
            _fail(
                source,
                line_number,
                column + len(token),
                "expected a CPU target after the signal name",
            )

        qualified, signal_column = items[0]
        if qualified.count(".") != 1:
            _fail(
                source,
                line_number,
                signal_column,
                "signal name must have the form <family>.<name>",
            )
        family, name = qualified.split(".", 1)
        if family not in _FAMILIES:
            _fail(
                source,
                line_number,
                signal_column,
                f"unknown runtime signal family {family!r}",
            )
        name_column = signal_column + len(family) + 1
        if _NAME.fullmatch(name) is None:
            _fail(
                source,
                line_number,
                name_column,
                "signal name must be a lowercase identifier",
            )

        target, target_column = items[1]
        if target == "<local>":
            cpu_target = None
        elif _CPU.fullmatch(target) is not None:
            cpu_target = int(target, 10)
        else:
            _fail(
                source,
                line_number,
                target_column,
                "CPU target must be <local> or a non-negative decimal logical ID",
            )

        arguments: list[int] = []
        for token, column in items[2:]:
            if _INTEGER.fullmatch(token) is None:
                _fail(
                    source,
                    line_number,
                    column,
                    "signal argument must be a signed decimal integer",
                )
            arguments.append(int(token, 10))

        if (family, name) == ("syscall", "exit"):
            if len(arguments) != 1:
                column = (
                    items[3][1]
                    if len(arguments) > 1
                    else target_column + len(target)
                )
                _fail(
                    source,
                    line_number,
                    column,
                    "syscall.exit requires exactly one status argument",
                )
            if not _I32_MIN <= arguments[0] <= _I32_MAX:
                _fail(
                    source,
                    line_number,
                    items[2][1],
                    "syscall.exit status is outside the signed 32-bit range",
                )

        signal = UserRuntimeSignal(
            family,
            name,
            cpu_target,
            tuple(arguments),
            source,
            line_number,
            signal_column,
        )
        result.append(signal)
        if (family, name) == ("syscall", "exit"):
            terminal_exit = signal
    return UserRuntimeSignalProgram(tuple(result))


def default_user_runtime_signals() -> UserRuntimeSignalProgram:
    """Return the in-memory default program without performing filesystem I/O."""

    return UserRuntimeSignalProgram(
        (
            UserRuntimeSignal(
                "syscall", "exit", None, (0,), "<default>", 1, 1
            ),
        )
    )
