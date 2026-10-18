"""Pulley: a block-and-tackle rigging planner driven by one plain-text rig file.

All physics runs in exact rational arithmetic (fractions.Fraction). Numbers are
only rounded when they are printed, so the same rig always gives the same answer.

Base units inside the program are kilograms (force and mass) and metres (length).
Display units are chosen with --units metric|imperial.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

KG_PER_LB = Fraction(45359237, 100000000)
M_PER_FT = Fraction(3048, 10000)
MAX_PARTS = 64
DEFAULT_MIN_SAFETY = Fraction(5)

LOAD_UNITS = {"kg": Fraction(1), "lb": KG_PER_LB}
LENGTH_UNITS = {"m": Fraction(1), "ft": M_PER_FT}
KEYS = (
    "name",
    "load",
    "lift",
    "reeving",
    "sheaves",
    "sheave_efficiency",
    "rope_wll",
    "min_safety",
)
REQUIRED = ("load", "lift", "reeving", "rope_wll")


@dataclass(frozen=True)
class Rig:
    """A parsed rig. Load and rope strength are in kg, lift in metres."""

    name: str
    load: Fraction
    lift: Fraction
    reeving: int
    sheaves: int
    sheave_efficiency: Fraction
    rope_wll: Fraction
    min_safety: Fraction


@dataclass
class ParseResult:
    rig: Rig | None
    errors: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class Solution:
    efficiency: Fraction
    actual_ma: Fraction
    effort: Fraction
    rope_travel: Fraction
    work_in: Fraction
    work_out: Fraction
    safety: Fraction
    passes: bool

    @property
    def friction_loss(self) -> Fraction:
        return self.work_in - self.work_out


@dataclass(frozen=True)
class Units:
    load: Fraction  # display units per kg
    load_label: str
    length: Fraction  # display units per metre
    length_label: str


UNIT_SYSTEMS = {
    "metric": Units(Fraction(1), "kg", Fraction(1), "m"),
    "imperial": Units(1 / KG_PER_LB, "lb", 1 / M_PER_FT, "ft"),
}


# --- parsing -----------------------------------------------------------------


def parse_number(text: str) -> Fraction:
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"'{text}' is not a number") from None


def parse_quantity(text: str, units: dict[str, Fraction], default_unit: str) -> Fraction:
    """Parse '1200', '1200 kg' or '2200 lb' and return the value in base units."""
    words = text.split()
    if len(words) == 1:
        number, unit = words[0], default_unit
    elif len(words) == 2:
        number, unit = words
    else:
        raise ValueError(f"expected a number with an optional unit, got '{text}'")
    unit = unit.lower()
    if unit not in units:
        raise ValueError(f"unknown unit '{unit}' (use {', '.join(units)})")
    return parse_number(number) * units[unit]


def parse_whole(text: str, minimum: int) -> int:
    value = parse_number(text)
    if value.denominator != 1 or value < minimum:
        raise ValueError(f"must be a whole number, at least {minimum}; got '{text}'")
    return int(value)


def parse_efficiency(text: str) -> Fraction:
    if text.endswith("%"):
        value = parse_number(text[:-1]) / 100
    else:
        value = parse_number(text)
    if not 0 < value <= 1:
        raise ValueError(f"must be above 0% and at most 100%; got '{text}'")
    return value


def _positive(value: Fraction) -> Fraction:
    if value <= 0:
        raise ValueError("must be greater than zero")
    return value


def _convert(key: str, text: str):
    if key == "name":
        return text
    if key == "load":
        return _positive(parse_quantity(text, LOAD_UNITS, "kg"))
    if key == "lift":
        return _positive(parse_quantity(text, LENGTH_UNITS, "m"))
    if key == "reeving":
        return parse_whole(text, 1)
    if key == "sheaves":
        return parse_whole(text, 0)
    if key == "sheave_efficiency":
        return parse_efficiency(text)
    if key == "rope_wll":
        return _positive(parse_quantity(text, LOAD_UNITS, "kg"))
    if key == "min_safety":
        value = parse_number(text)
        if value < 1:
            raise ValueError(f"must be at least 1; got '{text}'")
        return value
    raise ValueError(f"unknown key '{key}'")


def _line_of(message: str) -> int:
    """Line number a message refers to, so errors can be listed in file order."""
    if message.startswith("line "):
        return int(message.split(":", 1)[0][len("line ") :])
    return 0


def parse_rig(text: str) -> ParseResult:
    """Parse a rig file. Every problem is reported with its line number."""
    values: dict[str, str] = {}
    lines_of: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            errors.append(f"line {lineno}: expected 'key = value', got '{line}'")
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in KEYS:
            errors.append(f"line {lineno}: unknown key '{key}'")
            continue
        if not value:
            errors.append(f"line {lineno}: '{key}' has no value")
            continue
        if key in values:
            errors.append(
                f"line {lineno}: duplicate key '{key}' (first set on line {lines_of[key]})"
            )
            continue
        values[key] = value
        lines_of[key] = lineno

    for key in REQUIRED:
        if key not in values:
            errors.append(f"missing required key '{key}'")

    parsed: dict[str, object] = {}
    for key, value in values.items():
        try:
            parsed[key] = _convert(key, value)
        except ValueError as exc:
            errors.append(f"line {lines_of[key]}: {key}: {exc}")

    if errors:
        return ParseResult(None, sorted(errors, key=_line_of), warnings)

    rig = Rig(
        name=str(parsed.get("name", "Unnamed rig")),
        load=parsed["load"],
        lift=parsed["lift"],
        reeving=parsed["reeving"],
        sheaves=parsed.get("sheaves", 0),
        sheave_efficiency=parsed.get("sheave_efficiency", Fraction(1)),
        rope_wll=parsed["rope_wll"],
        min_safety=parsed.get("min_safety", DEFAULT_MIN_SAFETY),
    )

    if "sheave_efficiency" in parsed and rig.sheaves == 0 and rig.sheave_efficiency != 1:
        warnings.append(
            f"line {lines_of['sheave_efficiency']}: sheave_efficiency has no effect "
            "while sheaves = 0"
        )
    if rig.reeving > 1 and "sheaves" not in parsed:
        warnings.append("sheaves not given, so sheave friction is ignored")
    return ParseResult(rig, errors, warnings)


# --- physics -----------------------------------------------------------------


def efficiency(rig: Rig) -> Fraction:
    """Overall efficiency: each sheave the rope runs over multiplies in one factor."""
    return rig.sheave_efficiency**rig.sheaves


def actual_ma(rig: Rig) -> Fraction:
    """Actual mechanical advantage = supporting rope parts x overall efficiency."""
    return rig.reeving * efficiency(rig)


def solve(rig: Rig) -> Solution:
    eta = efficiency(rig)
    ma = rig.reeving * eta
    effort = rig.load / ma
    travel = rig.lift * rig.reeving
    work_in = effort * travel
    work_out = rig.load * rig.lift
    safety = rig.rope_wll / effort
    return Solution(
        efficiency=eta,
        actual_ma=ma,
        effort=effort,
        rope_travel=travel,
        work_in=work_in,
        work_out=work_out,
        safety=safety,
        passes=safety >= rig.min_safety,
    )


def smallest_reeving(rig: Rig, max_effort: Fraction) -> int | None:
    """Fewest rope parts that keep the hauling effort at or under max_effort."""
    for parts in range(1, MAX_PARTS + 1):
        if solve(replace(rig, reeving=parts)).effort <= max_effort:
            return parts
    return None


def safe_capacity(rig: Rig, max_effort: Fraction | None = None) -> tuple[Fraction, Fraction, str]:
    """Largest load the rig can lift, with the hauling-effort limit and its reason."""
    limit = rig.rope_wll / rig.min_safety
    reason = "rope strength divided by minimum safety factor"
    if max_effort is not None and max_effort < limit:
        limit = max_effort
        reason = "your --max-effort limit"
    return limit * actual_ma(rig), limit, reason


# --- formatting --------------------------------------------------------------


def fmt(value: Fraction, places: int = 2) -> str:
    """Round an exact value half-up for display."""
    with localcontext() as ctx:
        ctx.prec = 60
        number = Decimal(value.numerator) / Decimal(value.denominator)
        return str(number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _verdict(passes: bool) -> str:
    return "PASS" if passes else "FAIL"


def render_solution(rig: Rig, sol: Solution, units: Units) -> str:
    load_label, length_label = units.load_label, units.length_label
    work_scale = units.load * units.length
    rows = [
        ("Load", f"{fmt(rig.load * units.load)} {load_label}"),
        ("Lift", f"{fmt(rig.lift * units.length)} {length_label}"),
        ("Reeving", f"{rig.reeving} parts"),
        ("Sheaves", f"{rig.sheaves} at {fmt(rig.sheave_efficiency * 100)}% each"),
        ("Efficiency", f"{fmt(sol.efficiency * 100)}%"),
        ("Ideal MA", fmt(Fraction(rig.reeving))),
        ("Actual MA", fmt(sol.actual_ma)),
        ("Hauling effort", f"{fmt(sol.effort * units.load)} {load_label}"),
        ("Rope travel", f"{fmt(sol.rope_travel * units.length)} {length_label}"),
        ("Work in", f"{fmt(sol.work_in * work_scale)} {load_label}*{length_label}"),
        ("Work out", f"{fmt(sol.work_out * work_scale)} {load_label}*{length_label}"),
        ("Friction loss", f"{fmt(sol.friction_loss * work_scale)} {load_label}*{length_label}"),
        (
            "Rope safety",
            f"{fmt(sol.safety)}:1 (min {fmt(rig.min_safety)}:1) {_verdict(sol.passes)}",
        ),
    ]
    width = max(len(label) for label, _ in rows)
    lines = [f"Rig: {rig.name}"]
    lines += [f"  {label.ljust(width)}  {value}" for label, value in rows]
    return "\n".join(lines)


def solution_dict(rig: Rig, sol: Solution, units: Units) -> dict:
    return {
        "name": rig.name,
        "units": {"load": units.load_label, "length": units.length_label},
        "efficiency_percent": fmt(sol.efficiency * 100, 4),
        "ideal_ma": str(rig.reeving),
        "actual_ma": fmt(sol.actual_ma, 4),
        "effort": fmt(sol.effort * units.load, 4),
        "rope_travel": fmt(sol.rope_travel * units.length, 4),
        "work_in": fmt(sol.work_in * units.load * units.length, 4),
        "work_out": fmt(sol.work_out * units.load * units.length, 4),
        "friction_loss": fmt(sol.friction_loss * units.load * units.length, 4),
        "rope_safety": fmt(sol.safety, 4),
        "min_safety": fmt(rig.min_safety, 4),
        "pass": sol.passes,
    }


def explain(rig: Rig) -> str:
    sol = solve(rig)
    eta = sol.efficiency
    return "\n".join(
        [
            f"Rig: {rig.name}",
            f"1. Efficiency    eta = {fmt(rig.sheave_efficiency, 4)}^{rig.sheaves} = {fmt(eta, 4)}",
            f"2. Actual MA     MA  = {rig.reeving} x {fmt(eta, 4)} = {fmt(sol.actual_ma, 4)}",
            f"3. Hauling pull  F   = {fmt(rig.load)} / {fmt(sol.actual_ma, 4)} = {fmt(sol.effort)} kg",
            f"4. Rope travel   d   = {fmt(rig.lift)} x {rig.reeving} = {fmt(sol.rope_travel)} m",
            f"5. Work in       Win = {fmt(sol.effort, 4)} x {fmt(sol.rope_travel, 4)} = {fmt(sol.work_in)} kg*m",
            f"6. Work out      Wout= {fmt(rig.load)} x {fmt(rig.lift)} = {fmt(sol.work_out)} kg*m",
            f"7. Friction loss     = {fmt(sol.work_in)} - {fmt(sol.work_out)} = {fmt(sol.friction_loss)} kg*m",
            f"8. Rope safety   SF  = {fmt(rig.rope_wll)} / {fmt(sol.effort, 4)} = {fmt(sol.safety)} "
            f"(min {fmt(rig.min_safety)}) {_verdict(sol.passes)}",
        ]
    )


def render_table(rig: Rig, max_parts: int, units: Units) -> str:
    header = f"{'Parts':>5}  {'Actual MA':>9}  {'Effort':>10}  {'Travel':>9}  {'Rope SF':>8}  Verdict"
    lines = [f"Rig: {rig.name} (effort in {units.load_label}, travel in {units.length_label})", header]
    for parts in range(1, max_parts + 1):
        sol = solve(replace(rig, reeving=parts))
        lines.append(
            f"{parts:>5}  {fmt(sol.actual_ma):>9}  {fmt(sol.effort * units.load):>10}  "
            f"{fmt(sol.rope_travel * units.length):>9}  {fmt(sol.safety):>8}  "
            f"{_verdict(sol.passes)}"
        )
    return "\n".join(lines)


# --- command line ------------------------------------------------------------


def _load(path: str) -> Rig | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{path}: cannot read file ({exc.strerror})", file=sys.stderr)
        return None
    result = parse_rig(text)
    for message in result.errors:
        print(f"{path}: {message}", file=sys.stderr)
    for message in result.warnings:
        print(f"{path}: warning: {message}", file=sys.stderr)
    return result.rig


def _load_arg(text: str) -> Fraction:
    try:
        return parse_quantity(text, LOAD_UNITS, "kg")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _parts_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a whole number") from None
    if not 1 <= value <= MAX_PARTS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_PARTS}")
    return value


def cmd_check(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    sol = solve(rig)
    if not sol.passes:
        print(
            f"{args.file}: FAIL rope safety {fmt(sol.safety)}:1 is below the minimum "
            f"{fmt(rig.min_safety)}:1",
            file=sys.stderr,
        )
        return 2
    print(f"{args.file}: OK, rope safety {fmt(sol.safety)}:1 (min {fmt(rig.min_safety)}:1)")
    return 0


def cmd_solve(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    units = UNIT_SYSTEMS[args.units]
    sol = solve(rig)
    if args.json:
        print(json.dumps(solution_dict(rig, sol, units), indent=2))
    else:
        print(render_solution(rig, sol, units))
    return 0


def cmd_explain(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    print(explain(rig))
    return 0


def cmd_table(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    units = UNIT_SYSTEMS[args.units]
    if args.json:
        rows = []
        for parts in range(1, args.max + 1):
            row = solution_dict(rig, solve(replace(rig, reeving=parts)), units)
            row["reeving"] = parts
            rows.append(row)
        print(json.dumps(rows, indent=2))
    else:
        print(render_table(rig, args.max, units))
    return 0


def cmd_reeving(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    parts = smallest_reeving(rig, args.max_effort)
    if parts is None:
        print(
            f"{args.file}: no reeving up to {MAX_PARTS} parts keeps the effort at or under "
            f"{fmt(args.max_effort)} kg",
            file=sys.stderr,
        )
        return 2
    effort = solve(replace(rig, reeving=parts)).effort
    print(
        f"Smallest reeving: {parts} parts (effort {fmt(effort)} kg, "
        f"limit {fmt(args.max_effort)} kg)"
    )
    return 0


def cmd_capacity(args) -> int:
    rig = _load(args.file)
    if rig is None:
        return 1
    units = UNIT_SYSTEMS[args.units]
    capacity, limit, reason = safe_capacity(rig, args.max_effort)
    print(
        f"Safe lift capacity: {fmt(capacity * units.load)} {units.load_label}\n"
        f"Hauling effort limit: {fmt(limit * units.load)} {units.load_label} ({reason})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulley",
        description="Block-and-tackle rigging planner driven by one plain-text rig file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="lint a rig file and gate on rope safety")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("solve", help="print the full rig report")
    p.add_argument("file")
    p.add_argument("--units", choices=tuple(UNIT_SYSTEMS), default="metric")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("explain", help="show each formula with the numbers filled in")
    p.add_argument("file")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("table", help="compare reeving counts from 1 upwards")
    p.add_argument("file")
    p.add_argument("--max", type=_parts_arg, default=8, help="largest parts count (default 8)")
    p.add_argument("--units", choices=tuple(UNIT_SYSTEMS), default="metric")
    p.add_argument("--json", action="store_true", help="print the table as JSON")
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("reeving", help="find the fewest parts for a maximum effort")
    p.add_argument("file")
    p.add_argument("--max-effort", type=_load_arg, required=True, help="e.g. 400 or '880 lb'")
    p.set_defaults(func=cmd_reeving)

    p = sub.add_parser("capacity", help="largest load the rig can safely lift")
    p.add_argument("file")
    p.add_argument("--max-effort", type=_load_arg, default=None, help="optional effort cap")
    p.add_argument("--units", choices=tuple(UNIT_SYSTEMS), default="metric")
    p.set_defaults(func=cmd_capacity)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
