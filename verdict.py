# -*- coding: utf-8 -*-
"""sweep-verdict: 把「判定纪律」变成算术。

子命令：
  folds    逐折 Δ -> 均值 / 折间 sd / 同号率 / 门槛判定
  replace  单模提升 x 集成权重 -> 同位置替换的一阶估计
  cost     延迟 / 体积 -> 是否越过提交上限

退出码：0 通过，2 不通过（可直接当 CI 门禁），1 用法错误。
"""
import argparse
import math
import statistics
import sys

__version__ = "0.2.0"
MIN_FOLDS = 3


def fold_stats(deltas):
    values = [float(x) for x in deltas]
    n = len(values)
    mean = statistics.fmean(values) if values else 0.0
    sd = statistics.stdev(values) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    pos = sum(1 for x in values if x > 0)
    neg = sum(1 for x in values if x < 0)
    zero = n - pos - neg
    same = max(pos, neg)
    return {
        "n": n, "mean": mean, "sd": sd, "se": se,
        "pos": pos, "neg": neg, "zero": zero,
        "same_sign": same,
        "same_frac": (same / n) if n else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def fold_verdict(stats, min_gain, direction="up", min_same_frac=1.0):
    gain_ok = (stats["mean"] >= min_gain) if direction == "up" \
        else (stats["mean"] <= -min_gain)
    sign_ok = stats["same_frac"] >= min_same_frac
    enough = stats["n"] >= MIN_FOLDS
    if not enough:
        reason = f"折数 {stats['n']} < {MIN_FOLDS}：单折不进结论"
    elif not gain_ok:
        reason = "均值没过门槛"
    elif not sign_ok:
        reason = f"方向不一致（同号 {stats['same_sign']}/{stats['n']}）"
    else:
        reason = "均值过门槛且折间同号"
    return {
        "pass": enough and gain_ok and sign_ok,
        "gain_ok": gain_ok,
        "sign_ok": sign_ok,
        "enough_folds": enough,
        "reason": reason,
    }


def replace_estimate(member_gain, weight):
    """同位置替换的一阶估计：Δ集成 ≈ 集成权重 × 单模提升。"""
    return float(member_gain) * float(weight)


def scale_delta(proxy_delta, proxy_weight, real_weight):
    """代理集 Δ × (实包权重 / 代理权重)。"""
    proxy_weight = float(proxy_weight)
    if proxy_weight == 0:
        raise ValueError("proxy_weight 不能为 0")
    return float(proxy_delta) * float(real_weight) / proxy_weight


def cost_verdict(ms, limit_ms, mb=None, limit_mb=None):
    time_ok = ms <= limit_ms
    size_ok = True if mb is None or limit_mb is None else mb <= limit_mb
    return {"pass": time_ok and size_ok, "time_ok": time_ok, "size_ok": size_ok}


def _cmd_folds(args):
    stats = fold_stats(args.delta)
    verdict = fold_verdict(stats, args.min_gain, args.direction, args.min_same_frac)
    shown = " ".join(f"{float(x):+.4f}" for x in args.delta)
    print(f"折数 {stats['n']}  逐折 Δ: {shown}")
    print(f"均值 {stats['mean']:+.4f}  折间 sd {stats['sd']:.4f}  "
          f"se {stats['se']:.4f}  范围 [{stats['min']:+.4f}, {stats['max']:+.4f}]")
    print(f"同号 {stats['same_sign']}/{stats['n']}（正 {stats['pos']} / "
          f"负 {stats['neg']} / 零 {stats['zero']}）")
    print(f"门槛：|均值| >= {args.min_gain:.4f}，同号率 >= {args.min_same_frac:.0%}，"
          f"折数 >= {MIN_FOLDS}")
    if stats["sd"] > abs(stats["mean"]) > 0:
        print("  [warn] 折间 sd 大于均值：方向可能不稳，不要外推")
    print(f"[{'PASS' if verdict['pass'] else 'FAIL'}] {verdict['reason']}")
    return 0 if verdict["pass"] else 2


def _cmd_replace(args):
    estimate = replace_estimate(args.member_gain, args.weight)
    print(f"单模提升 {args.member_gain:+.4f} × 集成权重 {args.weight:.4f} "
          f"= 一阶估计 {estimate:+.4f}")
    print("  假设替换后与其余成员的相关性不变；新成员更互补才会更高，不能当保证")
    passed = True
    if args.target_gap is not None:
        enough = estimate >= args.target_gap
        passed = passed and enough
        print(f"目标缺口 {args.target_gap:+.4f} -> "
              f"{'够得到' if enough else '够不到，这个方向不必排队'}")
    if args.proxy_delta is not None:
        if args.proxy_weight is None or args.real_weight is None:
            print("[error] 需要同时给 --proxy-weight 和 --real-weight", file=sys.stderr)
            return 1
        real = scale_delta(args.proxy_delta, args.proxy_weight, args.real_weight)
        print(f"代理 Δ {args.proxy_delta:+.4f} × "
              f"({args.real_weight:g}/{args.proxy_weight:g}) = 实包 {real:+.4f}")
    return 0 if passed else 2


def _cmd_cost(args):
    verdict = cost_verdict(args.ms, args.limit_ms, args.mb, args.limit_mb)
    print(f"延迟 {args.ms:.2f} ms（上限 {args.limit_ms:.0f}）-> "
          f"{'PASS' if verdict['time_ok'] else 'FAIL'}")
    if args.mb is not None and args.limit_mb is not None:
        print(f"体积 {args.mb:.1f} MB（上限 {args.limit_mb:.0f}）-> "
              f"{'PASS' if verdict['size_ok'] else 'FAIL'}")
    print(f"[{'PASS' if verdict['pass'] else 'FAIL'}] "
          f"{'可以打包' if verdict['pass'] else '精度再高也不能交'}")
    return 0 if verdict["pass"] else 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sweep-verdict",
        description="Turn experiment-verdict discipline into arithmetic.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    folds = sub.add_parser("folds", help="judge per-fold deltas")
    folds.add_argument("delta", nargs="+", type=float, metavar="Δ")
    folds.add_argument("--min-gain", type=float, default=0.005)
    folds.add_argument("--direction", choices=["up", "down"], default="up")
    folds.add_argument("--min-same-frac", type=float, default=1.0)
    folds.set_defaults(func=_cmd_folds)

    replace = sub.add_parser("replace", help="estimate an ensemble swap")
    replace.add_argument("--member-gain", type=float, required=True)
    replace.add_argument("--weight", type=float, required=True)
    replace.add_argument("--target-gap", type=float)
    replace.add_argument("--proxy-delta", type=float)
    replace.add_argument("--proxy-weight", type=float)
    replace.add_argument("--real-weight", type=float)
    replace.set_defaults(func=_cmd_replace)

    cost = sub.add_parser("cost", help="check latency and size limits")
    cost.add_argument("--ms", type=float, required=True)
    cost.add_argument("--limit-ms", type=float, default=100.0)
    cost.add_argument("--mb", type=float)
    cost.add_argument("--limit-mb", type=float)
    cost.set_defaults(func=_cmd_cost)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
