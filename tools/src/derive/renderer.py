"""Stable human rendering for schema-v2 derivation results."""

from __future__ import annotations

from typing import TextIO

from .model import DerivationCheck, DerivationResult, DerivationUnit


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


def _check_line(kind: str, check: DerivationCheck, indent: str) -> str:
    if check.status == "passed":
        marker = "✓"
    elif check.status == "established":
        marker = "+"
    else:
        marker = "✗"
    return f"{indent}{kind} {check.expression} {marker}"


def _render_unit(
    unit: DerivationUnit,
    depth: int,
    names: dict[tuple[str, ...], str],
) -> list[str]:
    indent = "  " * depth
    detail_indent = "  " * (depth + 1)
    event = unit.event
    lines = [
        f"{indent}{unit.kind} {names[event.source]} -> {names[event.target]}: "
        f"{'::'.join(event.signal)}"
    ]
    lines.append(f"{detail_indent}enter {_special(unit.state_before)}")
    if unit.failure is not None and unit.failure.code == "undeclared_external_signal":
        lines.append(f"{detail_indent}external declaration ✗")
        return lines
    if unit.handler is None:
        lines.append(f"{detail_indent}handler {'::'.join(event.signal)} ✗")
        return lines
    lines.append(
        f"{detail_indent}handler {'::'.join(unit.handler)} -> "
        f"{_special(unit.candidate_state)}"
    )
    lines.extend(
        _check_line("depends_on", check, detail_indent)
        for check in unit.depends_on
    )
    for child in unit.drives:
        lines.extend(_render_unit(child, depth + 1, names))
    lines.extend(
        _check_line("ensures", check, detail_indent) for check in unit.ensures
    )
    lines.extend(
        _check_line("establishes", check, detail_indent)
        for check in unit.establishes
    )
    lines.extend(
        _check_line("invariant", check, detail_indent)
        for check in unit.invariants
    )
    if unit.state_after is not None:
        lines.append(
            f"{indent}{names[event.target]}: {_special(unit.state_before)} "
            f"→ {_special(unit.state_after)}"
        )
        for child in unit.emits:
            lines.extend(_render_unit(child, depth, names))
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
