# -*- coding: utf-8 -*-
"""sweep-audit CLI: 搜索空间审计的发布版入口。"""
import argparse
import json
import sys

import audit_flags as engine

__version__ = "0.1.0"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sweep-audit",
        description="Audit which declared CLI/config knobs were actually exercised.",
    )
    parser.add_argument("--sources", nargs="+", metavar="FILE",
                        help="argparse source files (default: train.py)")
    parser.add_argument("--commands", nargs="+", metavar="GLOB",
                        help="command/log globs (default: runs/**/*.cmd|bat|sh|ps1|log)")
    parser.add_argument("--configs", nargs="+", metavar="GLOB",
                        help="config globs (default: runs/**/*.json|yaml|yml|toml)")
    parser.add_argument("--run-root", metavar="DIR",
                        help="directory with one subdirectory per run (default: runs)")
    parser.add_argument("--prefix", help="CLI prefix (default: --)")
    parser.add_argument("--max-scan-mb", type=float, metavar="MB")
    parser.add_argument("--exclude", nargs="*", metavar="DIR",
                        help="directory names to skip")
    parser.add_argument("--not-applicable", nargs="*", metavar="FLAG",
                        help="flag names without -- (e.g. head); excluded from adjusted coverage")
    parser.add_argument("--known-used", nargs="*", metavar="FLAG=REASON",
                        help="NAME=REASON without --, e.g. 'head=fold0 0.772'")
    parser.add_argument("--json", dest="json_path", metavar="FILE",
                        help="write a machine-readable report")
    parser.add_argument("--fail-under", type=float, metavar="RATIO",
                        help="exit 2 if raw coverage is below this ratio (0..1)")
    parser.add_argument("--quiet", action="store_true", help="suppress the table")
    parser.add_argument("--verbose", action="store_true",
                        help="list scanned/evidence/uncovered paths")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _apply_known_used(rows, entries):
    by_name = {}
    for row in rows:
        by_name[row["opts"][0]] = row
        by_name[row["dest"]] = row
        by_name["--" + row["dest"]] = row
    marked = []
    for entry in entries or []:
        if "=" in entry:
            name, reason = entry.split("=", 1)
        elif ":" in entry:
            name, reason = entry.split(":", 1)
        else:
            name, reason = entry, ""
        name = name.strip()
        row = (by_name.get(name)
               or by_name.get(name.lstrip("-"))
               or by_name.get("--" + name.lstrip("-")))
        if row is None:
            print(f"[warn] --known-used did not match: {name}", file=sys.stderr)
            continue
        row["used"] = True
        row["state"] = "known_used"
        row["reason"] = reason.strip()
        marked.append(row)
    return marked


def _annotate(rows):
    for row in rows:
        row["name"] = row["opts"][0]
        row["kinds"] = engine.expected_kinds(row)
        row["swept"] = len(row["uniq"]) >= row["kinds"]


def _ratio(numerator, denominator):
    return (numerator / denominator) if denominator else 0.0


def _print_table(rows):
    print(f"{'参数':<24}{'默认':<14}{'记录数':>6}  取值")
    for row in rows:
        shown = ", ".join(row["uniq"][:6]) + (" ..." if len(row["uniq"]) > 6 else "")
        if row["state"] == "known_used":
            mark = f"   <== 手工标记已用：{row.get('reason') or '无理由'}"
        elif row["state"] == "confirmed_unused":
            mark = "   <== 确认没用"
        elif not row["used"]:
            mark = "   <== 无记录，待核实"
        elif row["swept"]:
            mark = ""
        else:
            mark = f"   <== 只碰过 {len(row['uniq'])}/{row['kinds']} 种取值"
        print(f"{row['name']:<24}{str(row['default']):<14}{len(row['raw']):>6}  {shown}{mark}")


def _summary(result, rows, not_applicable):
    total = len(rows)
    used = sum(1 for row in rows if row["used"])
    swept = sum(1 for row in rows if row["swept"])
    na_names = {str(x).lstrip("-").replace("-", "_") for x in (not_applicable or [])}
    applicable = [row for row in rows
                  if row["name"].lstrip("-").replace("-", "_") not in na_names]
    swept_app = sum(1 for row in applicable if row["swept"])
    return {
        "coverage": {
            "swept": swept,
            "total": total,
            "ratio": _ratio(swept, total),
            "adjusted_swept": swept_app,
            "adjusted_total": len(applicable),
            "adjusted_ratio": _ratio(swept_app, len(applicable)),
            "not_applicable": sorted(na_names),
        },
        "records": {
            "complete": bool(result["records_complete"]),
            "run_dirs": len(result["run_dirs"]),
            "uncovered_run_dirs": len(result["uncovered_run_dirs"]),
            "config_only_run_dirs": len(result["config_only_run_dirs"]),
            "skipped_files": len(result["skipped"]),
        },
        "scan": {
            "command_files": len(result["cmd_files"]),
            "config_files": len(result["cfg_files"]),
            "evidence_files": len(result["evidence_files"]),
        },
        "used": used,
    }


def _json_params(rows):
    payload = []
    for row in rows:
        payload.append({
            "name": row["name"],
            "dest": row["dest"],
            "default": row["default"],
            "type": row.get("type_name"),
            "action": row.get("action"),
            "choices": row.get("choices"),
            "count": len(row["raw"]),
            "values": row["uniq"],
            "expected_kinds": row["kinds"],
            "swept": row["swept"],
            "used": row["used"],
            "state": row["state"],
            "reason": row.get("reason"),
        })
    return payload


def run(argv=None):
    args = build_parser().parse_args(argv)
    if args.fail_under is not None and not 0 <= args.fail_under <= 1:
        print("[error] --fail-under must be between 0 and 1", file=sys.stderr)
        return 1
    if args.max_scan_mb is not None and args.max_scan_mb <= 0:
        print("[error] --max-scan-mb must be greater than 0", file=sys.stderr)
        return 1
    cfg = dict(engine.CONFIG)
    overrides = {
        "sources": args.sources,
        "command_globs": args.commands,
        "config_globs": args.configs,
        "run_root": args.run_root,
        "prefix": args.prefix,
        "max_scan_mb": args.max_scan_mb,
        "exclude_dirs": args.exclude,
        "not_applicable": args.not_applicable,
    }
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value

    result = engine.audit(cfg)
    rows = result["rows"]
    if not rows:
        print("no parameters parsed; check --sources and --prefix", file=sys.stderr)
        return 1

    _annotate(rows)
    known = _apply_known_used(rows, args.known_used)
    summary = _summary(result, rows, cfg.get("not_applicable"))
    coverage = summary["coverage"]

    if not args.quiet:
        _print_table(rows)
        print(f"\n参数总数 {coverage['total']}；有记录 {summary['used']} 个；"
              f"达到扫过标准 {coverage['swept']} 个")
        print(f"原始覆盖率 {coverage['ratio']:.0%}  —— 通常偏低，但不是严格数学下限")
        if coverage["not_applicable"]:
            print(f"调整后覆盖率 {coverage['adjusted_swept']} / {coverage['adjusted_total']} = "
                  f"{coverage['adjusted_ratio']:.0%}  —— 已剔除 "
                  f"{len(coverage['not_applicable'])} 个「不适用」参数")
        no_record = [row["name"] for row in rows if row["state"] == "unrecorded"]
        confirmed = [row["name"] for row in rows if row["state"] == "confirmed_unused"]
        print(f"无记录 {len(no_record)} 个（上限，待核实）："
              + (", ".join(no_record) if no_record else "无"))
        if confirmed:
            print(f"确认没用 {len(confirmed)} 个：" + ", ".join(confirmed))
        if known:
            print("手工标记已用：" + ", ".join(
                f"{row['name']}（{row.get('reason') or '无理由'}）" for row in known))
        records = summary["records"]
        print(f"\n扫描：命令文件 {summary['scan']['command_files']} 个，"
              f"配置文件 {summary['scan']['config_files']} 个，"
              f"运行目录 {records['run_dirs']} 个，"
              f"跳过超大文件 {records['skipped_files']} 个")
        print(f"记录完整性：{'完整' if records['complete'] else '不完整'}"
              f"（无命令记录的 run 目录 {records['uncovered_run_dirs']} 个，"
              f"其中只有配置的 {records['config_only_run_dirs']} 个）")
        if args.verbose:
            for path in result["cfg_files"]:
                print(f"  [cfg] {path}")
            for path in result["evidence_files"]:
                print(f"  [evidence] {path}")
            for path in result["uncovered_run_dirs"]:
                print(f"  [no-command] {path}")
            for path in result["skipped"]:
                print(f"  [skip] {path}")

    payload = {
        "schema": 1,
        "tool": "sweep-audit",
        "version": __version__,
        "summary": summary,
        "params": _json_params(rows),
        "unrecorded": [row["name"] for row in rows if row["state"] == "unrecorded"],
        "confirmed_unused": [row["name"] for row in rows
                             if row["state"] == "confirmed_unused"],
        "known_used": [{"name": row["name"], "reason": row.get("reason")}
                       for row in known],
    }
    if args.json_path:
        try:
            with open(args.json_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        except OSError as exc:
            print(f"[error] cannot write JSON: {exc}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"\nJSON 已写入 {args.json_path}")

    if args.fail_under is not None and coverage["ratio"] < args.fail_under:
        print(f"[fail] coverage {coverage['ratio']:.1%} < {args.fail_under:.1%}",
              file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
