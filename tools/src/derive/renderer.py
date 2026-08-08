"""Stable human rendering for schema-v2 derivation results."""

from __future__ import annotations

from typing import TextIO

from .model import DerivationResult, DerivationUnit


def _all_units(units: tuple[DerivationUnit, ...]):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
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
    if unit.status == "passed":
        committed = (
            "unchanged"
            if event.signal[0] == "Action"
            else _special(unit.state_after)
        )
        lines.append(f"{detail_indent}commit state: {committed}")
        for child in unit.emits:
            lines.extend(_render_unit(child, depth, names))
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
    lines.append("passed" if result.status == "passed" else f"stopped: {result.status}")
    stream.write("\n".join(lines))
    stream.write("\n")
