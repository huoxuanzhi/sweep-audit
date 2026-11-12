# -*- coding: utf-8 -*-
"""回归用例：判定纪律的四条算术（同号、门槛、权重折算、成本门禁）。"""
import contextlib
import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("vd", os.path.join(HERE, "verdict.py"))
vd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vd)


def quiet(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return vd.main(argv)


def close(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def main():
    cases = []

    def check(label, got, want):
        cases.append((label, got, want, got == want))

    base512 = [0.0111, 0.0046, 0.0104]
    stats = vd.fold_stats(base512)
    check("折数", stats["n"], 3)
    check("均值", close(stats["mean"], 0.0087, 1e-12), True)
    check("折间 sd", close(stats["sd"], 0.003568, 1e-6), True)
    check("同号率", stats["same_frac"], 1.0)
    check("三折同号通过", vd.fold_verdict(stats, 0.005)["pass"], True)

    single = vd.fold_stats([0.0002])
    check("单折不进结论", vd.fold_verdict(single, 0.005)["pass"], False)
    check("单折理由", "单折" in vd.fold_verdict(single, 0.005)["reason"], True)

    mixed = vd.fold_stats([0.02, 0.01, -0.001])
    check("方向不一致", vd.fold_verdict(mixed, 0.005)["pass"], False)
    check("放宽同号率后可过",
          vd.fold_verdict(mixed, 0.005, min_same_frac=0.6)["pass"], True)
    check("均值不够", vd.fold_verdict(vd.fold_stats([0.001, 0.002, 0.001]), 0.005)["pass"], False)

    down = vd.fold_stats([-0.02, -0.01, -0.015])
    check("下降方向", vd.fold_verdict(down, 0.005, direction="down")["pass"], True)
    check("下降方向遇正收益", vd.fold_verdict(vd.fold_stats([0.02, 0.01, 0.015]),
                                              0.005, direction="down")["pass"], False)

    check("替换一阶估计", close(vd.replace_estimate(0.0054, 1 / 6), 0.0009, 1e-4), True)
    check("代理折算", close(vd.scale_delta(0.0018, 0.2, 1 / 6), 0.0015, 1e-4), True)
    try:
        vd.scale_delta(0.0018, 0.0, 0.1667)
        raised = False
    except ValueError:
        raised = True
    check("零权重报错", raised, True)

    check("成本通过", vd.cost_verdict(82.63, 100, 453, 500)["pass"], True)
    check("延迟超限", vd.cost_verdict(105, 100, 453, 500)["pass"], False)
    check("体积超限", vd.cost_verdict(82.63, 100, 520, 500)["pass"], False)
    check("体积未给则不判", vd.cost_verdict(82.63, 100)["pass"], True)

    check("CLI 折间通过", quiet(["folds", *map(str, base512)]), 0)
    check("CLI 单折不通过", quiet(["folds", "0.0002"]), 2)
    check("CLI 替换够不到", quiet([
        "replace", "--member-gain", "0.0054", "--weight", "0.1667",
        "--target-gap", "0.0029",
        "--proxy-delta", "0.0018", "--proxy-weight", "0.2",
        "--real-weight", "0.1667",
    ]), 2)
    check("CLI 替换缺权重", quiet([
        "replace", "--member-gain", "0.0054", "--weight", "0.1667",
        "--proxy-delta", "0.0018",
    ]), 1)
    check("CLI 成本超限", quiet(["cost", "--ms", "105"]), 2)

    ok = sum(1 for *_, passed in cases if passed)
    for label, got, want, passed in cases:
        print(f"{'OK ' if passed else 'FAIL'} {label:<24} got={got!r} want={want!r}")
    print(f"\n{ok}/{len(cases)} 通过")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
