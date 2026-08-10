"""Stable human rendering for schema-v4 derivation results."""

from __future__ import annotations

import unicodedata
from typing import TextIO

from .model import DerivationResult, DerivationUnit


def _all_units(units: tuple[DerivationUnit, ...]):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)


def _shortest_names(result: DerivationResult) -> dict[tuple[str, ...], str]:
    names = {item.object for item in result.final_state}
    for unit in _all_units(result.units):
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


def _render_unit(
    unit: DerivationUnit,
    depth: int,
    names: dict[tuple[str, ...], str],
) -> list[str]:
    indent = "  " * depth
    detail_indent = "  " * (depth + 1)
    event = unit.event
    lines = [
        f"{indent}{names[event.source]} -> {names[event.target]}: "
        f"{event.mode}s {'::'.join(event.signal)}"
    ]
    lines.append(f"{detail_indent}current state: {_special(unit.state_before)}")
    for child in unit.drives:
        lines.extend(_render_unit(child, depth + 1, names))
    for child in unit.yields:
        lines.extend(_render_unit(child, depth + 1, names))
    for directive in unit.directives:
        marker = " ✗" if directive.kind == "panic" else ""
        lines.append(
            f"{detail_indent}{directive.kind}: {_single_line(directive.message)}{marker}"
        )
    if unit.status == "passed":
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
    elif unit.status == "yielded":
        generation = unit.yield_token_created.generation
        lines.append(
            f"{detail_indent}continuation yielded: generation {generation}"
        )
    else:
        lines.append(f"{detail_indent}commit state: not committed ✗")
    return lines


def render_derivation_result(result: DerivationResult, stream: TextIO) -> None:
    """Render a derivation record as deterministic, causal human output."""

    if not isinstance(result, DerivationResult):
        raise TypeError("result must be a DerivationResult")
    names = _shortest_names(result)
    lines: list[str] = []
    if result.units:
        for index, unit in enumerate(result.units):
            if index:
                lines.append("")
            lines.extend(_render_unit(unit, 0, names))
    elif result.failure is not None:
        features = ", ".join(result.failure.features)
        label = result.failure.message
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
        else f"stopped: {result.status}"
    )
    stream.write("\n".join(lines))
    stream.write("\n")
