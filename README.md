# Pulley

**Block-and-tackle rigging planner: one plain-text rig file in, exact effort, travel, friction and rope-safety numbers out.**

Pulley answers the questions you ask before hanging a load: how hard will I pull, how far does the rope run, how much does friction cost, is the rope strong enough, and what is the fewest parts of rope I need? Every number is exact rational arithmetic, so the same rig always gives the same answer. Nothing goes over the network.

## Features

- **Plain-text rig files** with `key = value` lines and `#` comments, so a rig is easy to write, diff and keep in version control.
- **Line-numbered errors**: unknown keys, duplicate keys, bad numbers and bad units all point at the exact line.
- **Lint warnings** for likely mistakes, such as a sheave efficiency with no sheaves or a multi-part rig with no sheaves listed.
- **Exact mechanical advantage**: ideal MA is the rope-part count; actual MA multiplies in the sheave efficiency, raised to the number of sheaves.
- **Hauling effort** for any load, including the friction that makes you pull harder than the ideal.
- **Rope travel**: how far you must haul to lift the load by the lift height.
- **Work balance**: work in, work out and friction loss, so you can see where the energy goes.
- **Rope safety factor** against the rope's working load limit, with a PASS/FAIL verdict and a CI-friendly exit code.
- **Step-by-step `explain`** that shows every formula with your numbers filled in.
- **Reeving table** that compares 1 to N parts side by side.
- **Smallest reeving search** for a maximum hauling effort you can manage.
- **Safe capacity** calculation: the heaviest load the rig can lift, limited by rope strength or by your own effort cap.
- **Metric and imperial** input and output (`kg`/`lb`, `m`/`ft`), converted with exact factors.
- **JSON output** for `solve` and `table`, for scripts and dashboards.

## Quick start

Save this as `examples/hoist.rig` (it is already in the repo):

```text
# Chain hoist on a gin pole, 4-part purchase
name = Gin pole hoist
load = 1200 kg
lift = 10 m
reeving = 4
sheaves = 4
sheave_efficiency = 96%
rope_wll = 2000 kg
min_safety = 5
```

Check it, then see the full report:

```console
$ python3 pulley.py check examples/hoist.rig
examples/hoist.rig: OK, rope safety 5.66:1 (min 5.00:1)

$ python3 pulley.py solve examples/hoist.rig
Rig: Gin pole hoist
  Load            1200.00 kg
  Lift            10.00 m
  Reeving         4 parts
  Sheaves         4 at 96.00% each
  Efficiency      84.93%
  Ideal MA        4.00
  Actual MA       3.40
  Hauling effort  353.21 kg
  Rope travel     40.00 m
  Work in         14128.51 kg*m
  Work out        12000.00 kg*m
  Friction loss   2128.51 kg*m
  Rope safety     5.66:1 (min 5.00:1) PASS
```

Compare reeving counts:

```console
$ python3 pulley.py table examples/hoist.rig --max 4
Rig: Gin pole hoist (effort in kg, travel in m)
Parts  Actual MA      Effort     Travel   Rope SF  Verdict
    1       0.85     1412.85      10.00      1.42  FAIL
    2       1.70      706.43      20.00      2.83  FAIL
    3       2.55      470.95      30.00      4.25  FAIL
    4       3.40      353.21      40.00      5.66  PASS
```

Find the fewest parts that keep the pull under 400 kg, and the largest load the rig can lift:

```console
$ python3 pulley.py reeving examples/hoist.rig --max-effort 400
Smallest reeving: 4 parts (effort 353.21 kg, limit 400.00 kg)

$ python3 pulley.py capacity examples/hoist.rig --max-effort 300
Safe lift capacity: 1019.22 kg
Hauling effort limit: 300.00 kg (your --max-effort limit)
```

Errors point at the line that caused them:

```console
$ python3 pulley.py check examples/bad.rig
examples/bad.rig: line 3: lift: unknown unit 'furlongs' (use m, ft)
examples/bad.rig: line 4: reeving: must be a whole number, at least 1; got '0'
examples/bad.rig: line 6: unknown key 'sheave_eficiency'
examples/bad.rig: line 8: duplicate key 'load' (first set on line 2)
```

## Rig file reference

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `name` | no | `Unnamed rig` | Label used in reports |
| `load` | yes | — | Weight to lift, `kg` or `lb` (no unit means `kg`) |
| `lift` | yes | — | Height to lift the load, `m` or `ft` (no unit means `m`) |
| `reeving` | yes | — | Whole number of rope parts supporting the load, at least 1 |
| `sheaves` | no | `0` | Sheave bearings the rope runs over, at least 0 |
| `sheave_efficiency` | no | `100%` | Efficiency of one sheave, as `96%` or `0.96` |
| `rope_wll` | yes | — | Rope working load limit, `kg` or `lb` |
| `min_safety` | no | `5` | Minimum rope safety factor the gate requires |

## How it works

With `n` rope parts, `s` sheaves and per-sheave efficiency `e`:

- Overall efficiency `eta = e^s`
- Actual MA `= n x eta`
- Hauling effort `F = load / (n x eta)`
- Rope travel `d = lift x n`
- Work in `= F x d`, work out `= load x lift`, friction loss `= work in - work out`
- Rope safety `SF = rope WLL / F`

## Command reference

| Command | What it does | Exit codes |
| --- | --- | --- |
| `check FILE` | Lints the file and gates on rope safety | `0` OK, `1` file errors, `2` safety below minimum |
| `solve FILE [--units metric\|imperial] [--json]` | Full rig report | `0` ok, `1` file errors |
| `explain FILE` | Every formula with the numbers filled in | `0` ok, `1` file errors |
| `table FILE [--max N] [--units ...] [--json]` | Reeving counts 1 to N (default 8, at most 64) | `0` ok, `1` file errors |
| `reeving FILE --max-effort LOAD` | Fewest parts for a maximum hauling effort | `0` found, `2` not reachable |
| `capacity FILE [--max-effort LOAD] [--units ...]` | Largest safe load, from rope strength or your effort cap | `0` ok, `1` file errors |

Run `python3 pulley.py --help` or `python3 pulley.py COMMAND --help` for the full options.

## Requirements

Python 3.10 or newer. Standard library only. No network access, no accounts, no API keys.

## How to run

```sh
python3 pulley.py check examples/hoist.rig
```

Use `check` in a CI job to fail the build when a rig's rope falls below its safety minimum:

```sh
python3 pulley.py check rigs/*.rig || exit 1
```

## How to test

Tests use pytest. Install it, then run from the project root:

```sh
python3 -m pip install pytest
python3 -m pytest -q
```

The suite checks parsing and line-numbered errors, every physics formula against hand-worked values, the reeving search and capacity limits, unit conversion, rounding, and each CLI command and exit code.

## Limits

Pulley models a single rig as one set of sheaves with one efficiency figure. It does not model rope stretch, dynamic loads, sheave diameter ratios, or how the sheave count changes as you add parts. Treat the output as a planning aid and follow your rigging standards for real lifts.
