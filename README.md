# sweep-audit

> Stop claiming you have exhausted the search space.

`sweep-audit` is a small, sharp audit tool for machine-learning experiments. It enumerates every CLI/config knob declared in your training code, mines your run history for evidence that each knob was actually exercised, and reports a **recorded coverage ratio** before you conclude "there is nothing left to tune".

It does not run experiments and it is not a hyperparameter optimizer. It audits the claim that the search is complete.

## The failure it prevents

A chest X-ray multi-label project ran 196 training jobs over 8 days. The model claimed the recipe was exhausted and the local ceiling was 0.8141. Then someone asked: *what about dropout?*

The training script had `--drop_path`. It had never been set in any of the 196 runs.

The full audit found 55 declared knobs. The first naive scanner reported 13 as "never used"; five of those were parser false positives. With a corrected scanner, the real picture was:

- 47 parameters had at least one recorded use
- 18 parameters reached the minimum exploration standard
- recorded coverage was 33%
- 8 parameters had no record at all

Adding the `.ps1` launchers and narrowing config files to the run directory changed the same code to 48 / 20 / 36% / 7. **The measurement protocol matters as much as the code.**

## What it reports

Every parameter lands in one of three states:

- **used**: at least one parseable record exists.
- **unrecorded, needs review**: no record exists, but the run history is incomplete. This is the default.
- **confirmed unused**: no record exists, every run directory has command evidence, and no file was skipped.

The tool also reports:

- **recorded coverage** = swept parameters / total parameters
- **adjusted coverage** after excluding parameters you mark as not applicable
- run-directory completeness, including how many runs have config only
- **default-only** parameters: recorded, but every value equals the default (for example `mixup_prob` seen twice, both `0.5`)
- **one-sided** sweeps: every numeric value sits on one side of the default (for example `img_size` tried at 384 and 518, never smaller)

The last two do not change coverage. They answer a question coverage cannot: *which direction was this knob actually pushed?*

The two headline numbers have directions:

- Coverage is usually a **lower bound** on real exploration, because runs without recorded commands are missed. It can also be inflated by prose or unrelated config files.
- The "unrecorded" list is an **upper bound** on truly unused parameters. It may contain parameters that were used but never recorded. `--head` in the case study is a known example: it ran at fold0 0.772, but its command was not saved.

## Quick start

Requires Python 3.9+. No dependencies beyond the standard library.

```bash
python sweep_audit.py \
  --sources train.py \
  --commands "runs/**/*.cmd" "runs/**/*.log" \
  --configs "runs/**/*.json" \
  --run-root runs
```

Or install the console script:

```bash
pip install -e .
sweep-audit --sources train.py --run-root runs
```

Run the bundled example:

```bash
python sweep_audit.py \
  --sources examples/toy_project/train.py \
  --commands "examples/toy_project/runs/**/*.cmd" \
  --configs "examples/toy_project/runs/**/*.json" \
  --run-root examples/toy_project/runs \
  --verbose
```

## Reading the output

```text
参数                      默认               记录数  取值
--lr                    0.001              3  0.001, 0.01, 0.1
--drop_path             0.0                1  0.1   <== 只碰过 1/3 种取值
--amp                   False              1  on
--loss                  bce                2  asl, bce
--seed                  42                 1  42   <== 只碰过 1/3 种取值
--head                                     0     <== 确认没用

参数总数 6；有记录 5 个；达到扫过标准 3 个
原始覆盖率 50%  —— 通常偏低，但不是严格数学下限
确认没用 1 个：--head
只碰过默认值 1 个：--seed
```

Sweep standard:

- boolean flags: one explicit appearance is enough, because the default state is the other state
- numeric parameters: `0.1`, `0.10`, and `1e-1` are normalized to one value
- parameters with `choices`: expected kinds = `min(3, len(choices))`
- everything else: at least 3 distinct values

## Useful options

| Option | Purpose |
|---|---|
| `--sources FILE...` | argparse source files |
| `--commands GLOB...` | launch scripts and logs; CLI-only matching |
| `--configs GLOB...` | JSON/YAML/TOML config files; key-only matching |
| `--run-root DIR` | one subdirectory per run, used for the three-state decision |
| `--known-used head=reason` | manually confirmed usage, e.g. a run whose command was lost |
| `--not-applicable seed` | exclude a parameter from adjusted coverage; use names without `--` |
| `--json report.json` | machine-readable report |
| `--fail-under 0.4` | exit 2 when recorded coverage is below the threshold |
| `--quiet` | suppress the table |
| `--verbose` | print scanned, evidence, uncovered, and skipped paths |

`--not-applicable` and `--known-used` take names without the leading `--`, so `seed` rather than `--seed`. This avoids argparse treating the value as another option.

## Verdict arithmetic

Coverage tells you what was tried. It does not tell you whether a result is real, large enough to matter, or shippable. `verdict.py` makes those three checks executable:

```bash
python verdict.py folds 0.0111 0.0046 0.0104 --min-gain 0.005
python verdict.py replace --member-gain 0.0054 --weight 0.1667 --target-gap 0.0029
python verdict.py cost --ms 105 --mb 453 --limit-mb 500
```

- `folds` rejects a single fold outright (`n < 3`) and requires both a mean above the threshold and the same sign on every fold.
- `replace` reports the first-order estimate `Δensemble ≈ weight × Δmember`, and rescales a proxy delta by `real_weight / proxy_weight`.
- `cost` fails on latency or size, so "accuracy went up but inference got too slow" is a hard fail instead of a footnote.

Exit code is `0` on pass and `2` on fail, so it works as a CI gate.

## JSON output

```json
{
  "summary": {
    "coverage": {
      "swept": 20,
      "total": 55,
      "ratio": 0.3636,
      "adjusted_swept": 20,
      "adjusted_total": 52,
      "adjusted_ratio": 0.3846,
      "not_applicable": ["head"]
    },
    "records": {
      "complete": false,
      "run_dirs": 111,
      "uncovered_run_dirs": 111,
      "config_only_run_dirs": 98
    },
    "direction": {
      "one_sided": 2,
      "default_only": 1
    }
  },
  "params": [
    {
      "name": "--lr",
      "values": ["0.1", "0.01"],
      "default_norm": "0.0001",
      "default_only": false,
      "one_sided": null,
      "expected_kinds": 3,
      "swept": false,
      "state": "used"
    }
  ],
  "unrecorded": ["--head"],
  "confirmed_unused": [],
  "known_used": []
}
```

## What it is not

- Not an HPO framework. It will not propose the next configuration.
- Not a proof that a parameter was never used. It only reports whether a record exists.
- Not a replacement for experiment tracking. It audits the tracking you already have on disk.

## Limitations

1. Argparse only. Click, tyro, Hydra, and Lightning CLI are not enumerated yet.
2. Matching is heuristic. A flag mentioned in prose or an unrelated config file can be counted as used.
3. A run is only "complete" if it has command evidence. A config-only run is deliberately not enough.
4. Command evidence is only as complete as the launch scripts you saved. Interactive runs are invisible.
5. Shell history, IDE run history, and platform submission logs are not read.
6. `--run-root` expects one subdirectory per run. A flat `runs/*.cmd` layout cannot reach `confirmed unused`, because there is no per-run directory to verify.

## Development

```bash
python test_audit.py
python test_cli.py
python test_verdict.py
```

The regression suite covers booleans, `store_false`, numeric normalization, untyped numeric inference, string case and leading zeros, `choices`, quoted values, YAML/TOML, aliases, `dest`, line continuations, inline comments, cross-line values, glob deduplication, oversized files, config-only runs, empty scripts, the three-state decision, default-only and one-sided detection, fold sign consistency, weight rescaling, and cost gates.

## License

MIT.
