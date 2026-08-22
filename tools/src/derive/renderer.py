"""Stable human rendering for schema-v10 multi-path derivation results."""

from __future__ import annotations

import unicodedata
from typing import TextIO

from .model import DerivationPath, DerivationResult, DerivationUnit
from model_ir import ModelExpression


def _all_units(units: tuple[DerivationUnit, ...]):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


def _shortest_names(path: DerivationPath) -> dict[tuple[str, ...], str]:
    names = {item.object for item in path.final_state}
    for unit in _all_units(path.units):
        names.add(unit.event.source)
        names.add(unit.event.target)
    ordered = tuple(sorted(names))
    rendered: dict[tuple[str, ...], str] = {}
    for name in ordered:
        for length in range(1, len(name) + 1):
            suffix = name[-length:]
            if sum(other[-length:] == suffix for other in ordered) == 1:
                rendered[name] = "::".join(suffix)
                break
    return rendered


def _special(name: tuple[str, ...] | None) -> str:
    return "<none>" if name is None else "::".join(name)


def _single_line(message: str) -> str:
    escapes = {
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    result: list[str] = []
    for character in message:
        if character in escapes:
            result.append(escapes[character])
        elif unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"}:
            codepoint = ord(character)
            width = 4 if codepoint <= 0xFFFF else 8
            prefix = "u" if width == 4 else "U"
            result.append(f"\\{prefix}{codepoint:0{width}x}")
        else:
            result.append(character)
    return "".join(result)


def _expression(expression: ModelExpression) -> str:
    if expression.kind in {"identifier", "integer"}:
        return str(expression.value)
    if expression.kind == "string":
        return repr(expression.value)
    if expression.kind in {"member", "path"}:
        separator = "." if expression.kind == "member" else "::"
        return f"{_expression(expression.children[0])}{separator}{expression.value}"
    if expression.kind == "call":
        return (
            f"{_expression(expression.children[0])}("
            + ", ".join(_expression(item) for item in expression.children[1:])
            + ")"
        )
    if expression.kind == "index":
        return f"{_expression(expression.children[0])}[{_expression(expression.children[1])}]"
    if expression.kind == "unary":
        return f"{expression.value}{_expression(expression.children[0])}"
    if expression.kind == "binary":
        return (
            f"({_expression(expression.children[0])} {expression.value} "
            f"{_expression(expression.children[1])})"
        )
    return f"<{expression.kind}>"


def _render_unit(
    unit: DerivationUnit,
    depth: int,
    names: dict[tuple[str, ...], str],
) -> list[str]:
    indent = "  " * depth
    detail_indent = "  " * (depth + 1)
    event = unit.event
    arguments = (
        "(" + ", ".join(_expression(item) for item in event.arguments) + ")"
        if event.arguments
        else ""
    )
    lines = [
        f"{indent}{names[event.source]} -> {names[event.target]}: "
        f"{event.mode}s {'::'.join(event.signal)}{arguments}"
    ]
    lines.append(f"{detail_indent}current state: {_special(unit.state_before)}")
    for drive_index in range(len(unit.drives) + 1):
        for switch in unit.switches:
            if switch.after_drives != drive_index:
                continue
            fallback = " (idle fallback)" if switch.idle_fallback else ""
            closed = " [cycle closed]" if switch.cycle_closed else ""
            lines.append(
                f"{detail_indent}switches {switch.binding} = "
                f"{names[switch.task]}{fallback}{closed}"
            )
        if drive_index < len(unit.drives):
            lines.extend(_render_unit(unit.drives[drive_index], depth + 1, names))
    for child in unit.yields:
        lines.extend(_render_unit(child, depth + 1, names))
    for directive in unit.directives:
        marker = " ✗" if directive.kind == "panic" else ""
        lines.append(
            f"{detail_indent}{directive.kind}: {_single_line(directive.message)}{marker}"
        )
    if unit.status in {"passed", "cycle_closed"}:
        committed = (
            "unchanged"
            if event.signal[0] == "Action"
            else _special(unit.state_after)
        )
        lines.append(f"{detail_indent}commit state: {committed}")
        for child in unit.emits:
            if depth == 0:
                lines.append("")
            lines.extend(_render_unit(child, depth, names))
        for child in unit.resumes:
            if depth == 0:
                lines.append("")
            lines.extend(_render_unit(child, depth, names))
    elif unit.status != "yielded":
        lines.append(f"{detail_indent}commit state: not committed ✗")
    return lines


def render_derivation_result(result: DerivationResult, stream: TextIO) -> None:
    """Render a derivation record as deterministic, causal human output."""

    if not isinstance(result, DerivationResult):
        raise TypeError("result must be a DerivationResult")
    lines: list[str] = []
    multiple = len(result.paths) > 1
    for path_index, path in enumerate(result.paths):
        if path_index:
            lines.append("")
        if multiple:
            lines.append(f"Path {path_index + 1} [{path.status}]")
        names = _shortest_names(path)
        if path.units:
            for index, unit in enumerate(path.units):
                if index:
                    lines.append("")
                lines.extend(_render_unit(unit, 0, names))
        elif path.failure is not None:
            features = ", ".join(path.failure.features)
            label = path.failure.message
            if features:
                label = f"{label}: {features}"
            lines.append(f"{label} ✗")
    if lines:
        lines.append("")
    lines.append(
        "Derivation passed!"
        if result.status == "passed"
        else "Derivation yielded!"
        if result.status == "yielded"
        else f"stopped: {result.paths[0].status}"
        if len(result.paths) == 1
        else "Derivation failed!"
    )
    stream.write("\n".join(lines))
    stream.write("\n")
