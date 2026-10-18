import json
from fractions import Fraction
from pathlib import Path

import pytest

import pulley
from pulley import (
    KG_PER_LB,
    UNIT_SYSTEMS,
    Rig,
    actual_ma,
    efficiency,
    explain,
    fmt,
    main,
    parse_quantity,
    parse_rig,
    render_table,
    safe_capacity,
    smallest_reeving,
    solve,
)

ROOT = Path(__file__).resolve().parent.parent
HOIST = ROOT / "examples" / "hoist.rig"
SLACK = ROOT / "examples" / "slack-rope.rig"
BAD = ROOT / "examples" / "bad.rig"

HOIST_TEXT = HOIST.read_text(encoding="utf-8")

ETA = Fraction(24, 25) ** 4  # 96% per sheave over 4 sheaves


def make_rig(**overrides) -> Rig:
    fields = dict(
        name="test",
        load=Fraction(1200),
        lift=Fraction(10),
        reeving=4,
        sheaves=0,
        sheave_efficiency=Fraction(1),
        rope_wll=Fraction(2000),
        min_safety=Fraction(5),
    )
    fields.update(overrides)
    return Rig(**fields)


def test_parses_example_rig_fields():
    result = parse_rig(HOIST_TEXT)
    assert result.errors == []
    rig = result.rig
    assert rig.name == "Gin pole hoist"
    assert rig.load == 1200
    assert rig.lift == 10
    assert rig.reeving == 4
    assert rig.sheaves == 4
    assert rig.sheave_efficiency == Fraction(24, 25)
    assert rig.rope_wll == 2000
    assert rig.min_safety == 5


def test_percent_and_fraction_efficiency_agree():
    percent = parse_rig("load = 1 kg\nlift = 1 m\nreeving = 2\nrope_wll = 9\nsheave_efficiency = 96%")
    fraction = parse_rig("load = 1 kg\nlift = 1 m\nreeving = 2\nrope_wll = 9\nsheave_efficiency = 0.96")
    assert percent.rig.sheave_efficiency == fraction.rig.sheave_efficiency == Fraction(24, 25)


def test_imperial_units_convert_exactly_to_base_units():
    assert parse_quantity("2200 lb", pulley.LOAD_UNITS, "kg") == 2200 * KG_PER_LB
    result = parse_rig("load = 10 lb\nlift = 2 ft\nreeving = 1\nrope_wll = 1 kg")
    assert result.errors == []
    assert result.rig.lift == 2 * pulley.M_PER_FT


def test_errors_carry_line_numbers():
    result = parse_rig(BAD.read_text(encoding="utf-8"))
    assert result.rig is None
    joined = "\n".join(result.errors)
    assert "line 3: lift: unknown unit 'furlongs'" in joined
    assert "line 4: reeving: must be a whole number, at least 1" in joined
    assert "line 6: unknown key 'sheave_eficiency'" in joined
    assert "line 8: duplicate key 'load' (first set on line 2)" in joined


def test_missing_required_keys_are_reported():
    result = parse_rig("name = bare\nload = 5 kg")
    assert result.rig is None
    assert "missing required key 'lift'" in result.errors
    assert "missing required key 'reeving'" in result.errors
    assert "missing required key 'rope_wll'" in result.errors


def test_efficiency_above_one_hundred_percent_is_rejected():
    result = parse_rig("load = 1 kg\nlift = 1 m\nreeving = 2\nrope_wll = 9 kg\nsheave_efficiency = 101%")
    assert result.rig is None
    assert any("sheave_efficiency: must be above 0% and at most 100%" in e for e in result.errors)


def test_warns_when_efficiency_set_without_sheaves():
    text = "load = 1 kg\nlift = 1 m\nreeving = 1\nrope_wll = 9 kg\nsheave_efficiency = 90%"
    result = parse_rig(text)
    assert result.rig is not None
    assert result.warnings == [
        "line 5: sheave_efficiency has no effect while sheaves = 0",
    ]


def test_warns_when_multi_part_rig_has_no_sheaves_listed():
    result = parse_rig("load = 1 kg\nlift = 1 m\nreeving = 3\nrope_wll = 9 kg")
    assert result.rig is not None
    assert result.warnings == ["sheaves not given, so sheave friction is ignored"]


def test_ideal_effort_without_friction_is_exact():
    sol = solve(make_rig(reeving=4, sheaves=0))
    assert sol.efficiency == 1
    assert sol.actual_ma == 4
    assert sol.effort == 300


def test_actual_ma_uses_exact_efficiency_power():
    rig = make_rig(reeving=4, sheaves=4, sheave_efficiency=Fraction(24, 25))
    assert efficiency(rig) == ETA
    assert actual_ma(rig) == 4 * ETA
    assert float(actual_ma(rig)) == pytest.approx(3.39738624)


def test_solve_hoist_matches_hand_calculation():
    sol = solve(parse_rig(HOIST_TEXT).rig)
    assert sol.effort == Fraction(1200) / (4 * ETA)
    assert float(sol.effort) == pytest.approx(353.2127, abs=1e-4)
    assert sol.rope_travel == 40
    assert sol.work_out == 12000
    assert sol.work_in == 12000 / ETA
    assert float(sol.friction_loss) == pytest.approx(2128.51, abs=0.01)
    assert float(sol.safety) == pytest.approx(5.6623, abs=1e-4)
    assert sol.passes is True


def test_work_in_always_exceeds_work_out_under_friction():
    sol = solve(make_rig(reeving=3, sheaves=3, sheave_efficiency=Fraction(9, 10)))
    assert sol.friction_loss > 0
    assert sol.work_in == sol.work_out / sol.efficiency


def test_weak_rope_fails_safety_gate():
    sol = solve(parse_rig(SLACK.read_text(encoding="utf-8")).rig)
    assert float(sol.safety) == pytest.approx(2.8311, abs=1e-4)
    assert sol.passes is False


def test_smallest_reeving_for_effort_limit():
    rig = parse_rig(HOIST_TEXT).rig
    assert smallest_reeving(rig, Fraction(400)) == 4
    assert smallest_reeving(rig, Fraction(1000)) == 2
    assert smallest_reeving(rig, Fraction(2000)) == 1


def test_smallest_reeving_returns_none_when_unreachable():
    rig = parse_rig(HOIST_TEXT).rig
    assert smallest_reeving(rig, Fraction(1)) is None


def test_safe_capacity_is_limited_by_rope_then_by_max_effort():
    rig = parse_rig(HOIST_TEXT).rig
    capacity, limit, reason = safe_capacity(rig)
    assert limit == 400
    assert "rope strength" in reason
    assert float(capacity) == pytest.approx(400 * 3.39738624, abs=1e-6)

    capacity, limit, reason = safe_capacity(rig, Fraction(300))
    assert limit == 300
    assert reason == "your --max-effort limit"
    assert float(capacity) == pytest.approx(300 * 3.39738624, abs=1e-6)


def test_fmt_rounds_half_up_and_keeps_places():
    assert fmt(Fraction(1, 3)) == "0.33"
    assert fmt(Fraction(2345, 1000)) == "2.35"
    assert fmt(Fraction(5)) == "5.00"
    assert fmt(Fraction(1200) / KG_PER_LB) == "2645.55"


def test_explain_shows_each_formula_with_numbers():
    text = explain(parse_rig(HOIST_TEXT).rig)
    assert "eta = 0.9600^4 = 0.8493" in text
    assert "Actual MA" in text
    assert "353.21" in text
    assert "Rope safety" in text


def test_table_lists_every_reeving_count():
    rig = parse_rig(HOIST_TEXT).rig
    lines = render_table(rig, 4, UNIT_SYSTEMS["metric"]).splitlines()
    assert len(lines) == 2 + 4  # title, header, one row per parts count
    assert lines[-1].split()[0] == "4"
    assert lines[-1].endswith("PASS")


def test_cli_check_passes_on_example(capsys):
    assert main(["check", str(HOIST)]) == 0
    assert "OK, rope safety 5.66:1" in capsys.readouterr().out


def test_cli_check_reports_errors_with_exit_1(capsys):
    assert main(["check", str(BAD)]) == 1
    err = capsys.readouterr().err
    assert f"{BAD}: line 3: lift: unknown unit 'furlongs'" in err


def test_cli_check_fails_safety_gate_with_exit_2(capsys):
    assert main(["check", str(SLACK)]) == 2
    assert "FAIL rope safety 2.83:1" in capsys.readouterr().err


def test_cli_solve_json_round_trips(capsys):
    assert main(["solve", str(HOIST), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["effort"] == "353.2127"
    assert data["pass"] is True
    assert data["units"] == {"load": "kg", "length": "m"}


def test_cli_solve_imperial_units(capsys):
    assert main(["solve", str(HOIST), "--units", "imperial"]) == 0
    out = capsys.readouterr().out
    assert "Hauling effort" in out
    assert " lb" in out
    assert " ft" in out


def test_cli_table_json_has_row_per_parts(capsys):
    assert main(["table", str(HOIST), "--max", "3", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["reeving"] for row in rows] == [1, 2, 3]


def test_cli_reeving_prints_smallest_parts(capsys):
    assert main(["reeving", str(HOIST), "--max-effort", "400"]) == 0
    assert "Smallest reeving: 4 parts" in capsys.readouterr().out


def test_cli_reeving_unreachable_exits_2(capsys):
    assert main(["reeving", str(HOIST), "--max-effort", "1"]) == 2
    assert "no reeving up to 64 parts" in capsys.readouterr().err


def test_cli_capacity_with_effort_cap(capsys):
    assert main(["capacity", str(HOIST), "--max-effort", "300"]) == 0
    out = capsys.readouterr().out
    assert "Safe lift capacity: 1019.22 kg" in out


def test_cli_missing_file_exits_1(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope.rig")]) == 1
    assert "cannot read file" in capsys.readouterr().err
