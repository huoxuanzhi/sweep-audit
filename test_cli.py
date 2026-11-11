# -*- coding: utf-8 -*-
"""CLI 回归：JSON、known-used、not-applicable、fail-under、quiet。"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sweep_audit


def base_args():
    root = os.path.join(HERE, "examples", "toy_project")
    return [
        "--sources", os.path.join(root, "train.py"),
        "--commands", os.path.join(root, "runs", "**", "*.cmd"),
        "--configs", os.path.join(root, "runs", "**", "*.json"),
        "--run-root", os.path.join(root, "runs"),
    ]


def main():
    cases = []

    def check(label, got, want):
        cases.append((label, got, want, got == want))

    out_dir = tempfile.mkdtemp()
    report = os.path.join(out_dir, "report.json")
    code = sweep_audit.run(base_args() + ["--json", report])
    check("正常退出码", code, 0)
    with open(report, encoding="utf-8") as handle:
        data = json.load(handle)
    check("参数总数", data["summary"]["coverage"]["total"], 6)
    check("扫过数", data["summary"]["coverage"]["swept"], 3)
    check("覆盖率", round(data["summary"]["coverage"]["ratio"], 2), 0.5)
    check("记录完整", data["summary"]["records"]["complete"], True)
    by_name = {item["name"]: item for item in data["params"]}
    check("--head 确认没用", by_name["--head"]["state"], "confirmed_unused")
    check("--drop_path 取值", by_name["--drop_path"]["values"], ["0.1"])

    report_known = os.path.join(out_dir, "known.json")
    code = sweep_audit.run(base_args() + [
        "--known-used", "head=fold0 0.772, command missing",
        "--json", report_known,
        "--quiet",
    ])
    check("known-used 退出码", code, 0)
    with open(report_known, encoding="utf-8") as handle:
        known = json.load(handle)
    head = next(item for item in known["params"] if item["name"] == "--head")
    check("known-used 状态", head["state"], "known_used")
    check("known-used 原因", head["reason"], "fold0 0.772, command missing")
    check("known-used 不再确认没用", known["confirmed_unused"], [])

    report_na = os.path.join(out_dir, "na.json")
    sweep_audit.run(base_args() + [
        "--not-applicable", "seed",
        "--json", report_na,
        "--quiet",
    ])
    with open(report_na, encoding="utf-8") as handle:
        na = json.load(handle)
    check("调整后分母", na["summary"]["coverage"]["adjusted_total"], 5)
    check("调整后比例", round(na["summary"]["coverage"]["adjusted_ratio"], 2), 0.6)

    code = sweep_audit.run(base_args() + ["--quiet", "--fail-under", "0.9"])
    check("fail-under 退出码", code, 2)

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        sweep_audit.run(base_args() + ["--quiet"])
    check("quiet 无表格", buffer.getvalue().strip(), "")

    passed = sum(1 for *_, ok in cases if ok)
    for label, got, want, ok in cases:
        print(f"{'OK ' if ok else 'FAIL'} {label:<22} got={got!r} want={want!r}")
    print(f"\n{passed}/{len(cases)} 通过")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
