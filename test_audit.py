# -*- coding: utf-8 -*-
"""回归用例：来自真实故障模式，而不是实现分支。"""
import importlib.util
import json
import os
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("af", os.path.join(HERE, "audit_flags.py"))
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def train_source(path, extra=""):
    write(path, textwrap.dedent(
        """\
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--lr", type=float, default=1e-4)
        ap.add_argument("--lr_infer", default=1e-3)
        ap.add_argument("--int_infer", default=0)
        ap.add_argument("--amp", action="store_true")
        ap.add_argument("--epochs", type=int, default=4)
        ap.add_argument("--drop-path", "--dp", dest="drop_path", type=float, default=0.0)
        ap.add_argument("--loss", choices=["asl", "bce"], default="bce")
        ap.add_argument("--note", default="")
        ap.add_argument("--tag", default="")
        ap.add_argument("--model", default="tiny")
        ap.add_argument("--seed", type=int, default=42)
        ap.add_argument("--foo", "--foo-bar", default="")
        ap.add_argument("--no-pretrain", dest="pretrained", action="store_false")
        ap.add_argument("--unused-a", type=float, default=0.0)
        ap.add_argument("--unused-b", type=int, default=0)
        ap.add_argument("--unused-c", type=int, default=0)
        ap.add_argument("--width", type=float, default=0.5)
        ap.add_argument("--depth", type=int, default=4)
        ap.add_argument("--fixed", type=float, default=0.5)
        """
    ) + extra)


def build_fixture(root):
    """覆盖：引号空格、注释、续行、别名、dest、大小写、前导零。"""
    runs = os.path.join(root, "runs")
    train_source(os.path.join(root, "train.py"))
    write(os.path.join(runs, "a.cmd"), textwrap.dedent(
        """\
        REM --unused-b 3
        --lr 0.1 --lr_infer 1e-1 --amp --drop_path ^
        0.1 --loss "asl" --model tiny --note "hello world" --foo x  # --unused-c 3
        """
    ))
    write(os.path.join(runs, "b.sh"),
          "--seed 42 --lr 1e-1 --lr_infer 0.1 --int_infer 001 --loss bce --dp 0.3 "
          "--model ResNet50 --tag 001 --width 1.5 --width 2.0 --depth 2 --depth 3\n")
    write(os.path.join(runs, "c.json"), json.dumps(
        {"seed": 7, "lr_infer": 0.10, "int_infer": 1, "model": "resnet50",
         "drop_path": 0.2, "foo": "x", "tag": 1, "fixed": 0.5}
    ))
    write(os.path.join(runs, "d.yaml"), 'seed: 99\nnote: "my note"\n')
    # 这一行看起来像配置，但它是日志；不能被 YAML 扫描污染 --lr。
    write(os.path.join(runs, "e.log"), "epoch 1  lr: 0.5  loss: 0.1\n")
    write(os.path.join(runs, "big.log"), "x" * (6 * 1024 * 1024))


def row(result, name):
    for item in result["rows"]:
        if item["opts"][0] == name:
            return item
    raise KeyError(name)


def main():
    root = tempfile.mkdtemp()
    build_fixture(root)
    cfg = dict(
        sources=[os.path.join(root, "train.py")],
        command_globs=[
            os.path.join(root, "runs", "*.cmd"),
            os.path.join(root, "runs", "*.sh"),
            os.path.join(root, "runs", "*.log"),
        ],
        config_globs=[
            os.path.join(root, "runs", "*.json"),
            os.path.join(root, "runs", "*.yaml"),
        ],
        run_root=os.path.join(root, "runs"),
        max_scan_mb=5,
    )
    result = af.audit(cfg)

    cases = []

    def check(label, got, want):
        cases.append((label, got, want, got == want))

    # 1 布尔：出现即算试过；store_false 记 off
    check("布尔 amp 记录", row(result, "--amp")["uniq"], ["on"])
    check(
        "布尔 amp 判定",
        len(row(result, "--amp")["uniq"]) >= af.expected_kinds(row(result, "--amp")),
        True,
    )
    check(
        "store_false 取值",
        af.scan_cli("--no-pretrain", ["--no-pretrain"], "--", True, bool_on=False),
        ["off"],
    )

    # 2 数值归一；int 参数归一，字符串参数保留前导零
    check("float 归一 lr", row(result, "--lr")["uniq"], ["0.1"])
    check("无 type 浮点推断", row(result, "--lr_infer")["uniq"], ["0.1"])
    check("无 type 整数推断", row(result, "--int_infer")["uniq"], ["1"])
    check("int 归一 seed", "99" in row(result, "--seed")["uniq"], True)
    check("字符串保留前导零", row(result, "--tag")["uniq"], ["001", "1"])
    check("字符串保留大小写", "ResNet50" in row(result, "--model")["uniq"]
          and "resnet50" in row(result, "--model")["uniq"], True)

    # 3 choices 只有 2 项时，覆盖两项即算扫过
    check("choices 覆盖", row(result, "--loss")["uniq"], ["asl", "bce"])
    check(
        "choices 判定",
        len(row(result, "--loss")["uniq"]) >= af.expected_kinds(row(result, "--loss")),
        True,
    )

    # 4 引号值：CLI 和 YAML 都要完整捕获空格
    check("CLI 引号空格", row(result, "--note")["uniq"], ["hello world", "my note"])
    check("CLI 等号引号空格",
          af.scan_cli('--note="hello world"', ["--note"], "--", False),
          ["hello world"])
    toml_values = []
    af.scan_config_text('model = "my model"\n', "model", toml_values, ".toml")
    check("TOML 引号空格", toml_values, ["my model"])

    # 5 JSON/YAML 分开；YAML 顶格键要算，日志缩进行不污染
    check("YAML 顶格 seed", "99" in row(result, "--seed")["uniq"], True)
    check("日志缩进行不污染 lr", "0.5" in row(result, "--lr")["uniq"], False)

    # 6 Namespace 不计数
    check("Namespace 不计",
          af.scan_cli("Namespace(drop_path=0.1)", ["--drop_path"], "--", False), [])

    # 7 dest / 别名：--dp、--foo-bar 的 dest 和 JSON 键都要对上
    check("别名 --dp", "0.3" in row(result, "--drop-path")["uniq"], True)
    check("JSON 用 dest", "0.2" in row(result, "--drop-path")["uniq"], True)
    check("第一个长选项定 dest", row(result, "--foo")["uniq"], ["x"])

    # 8 续行不产生 "^"；注释里的参数不算
    check("续行", "^" in row(result, "--drop-path")["uniq"], False)
    check("注释不计数", row(result, "--unused-b")["used"], False)
    check("行内注释不计数", row(result, "--unused-c")["used"], False)
    check("引号内 # 保留",
          af._strip_inline_hash('--note "hello # world" # comment'),
          '--note "hello # world"')
    check("跨行取值", af.scan_cli("--lr\n0.1", ["--lr"], "--", False), [])
    check("跨行命令不取值", af.scan_cli("--lr\npython train.py --amp", ["--lr"], "--", False), [])

    # 9 去重 glob + 超大文件
    duplicate = af.audit(dict(cfg, command_globs=cfg["command_globs"] * 2))
    check("glob 去重", len(duplicate["cmd_files"]), len(result["cmd_files"]))
    check("超大文件被列出", any("big.log" in p for p in result["skipped"]), True)

    # 10 三态：只有命令记录完整时，才允许“确认没用”
    check("未使用 unused_a", row(result, "--unused-a")["used"], False)
    check("无记录状态", row(result, "--unused-a")["state"], "unrecorded")

    # 11 P0：只有 config.json 的 run 不能被当成记录完整
    config_only_root = tempfile.mkdtemp()
    train_source(os.path.join(config_only_root, "train.py"), 'ap.add_argument("--head", default="")\n')
    write(os.path.join(config_only_root, "runs", "run1", "config.json"), '{"other": 1}')
    config_only = af.audit(dict(
        sources=[os.path.join(config_only_root, "train.py")],
        command_globs=[os.path.join(config_only_root, "runs", "**", "*.cmd")],
        config_globs=[os.path.join(config_only_root, "runs", "**", "*.json")],
        run_root=os.path.join(config_only_root, "runs"),
        max_scan_mb=5,
    ))
    check("config-only run 不完整", config_only["records_complete"], False)
    check("config-only run 状态", row(config_only, "--head")["state"], "unrecorded")

    # 11b P0 后门：空脚本/纯注释脚本不能算命令证据
    check("空脚本不是证据", af.is_command_evidence("placeholder.cmd", "", "--"), False)
    check("纯注释脚本不是证据", af.is_command_evidence("placeholder.cmd", "REM todo\n", "--"), False)
    empty_root = tempfile.mkdtemp()
    train_source(os.path.join(empty_root, "train.py"), 'ap.add_argument("--head", default="")\n')
    write(os.path.join(empty_root, "runs", "run1", "placeholder.cmd"), "")
    empty = af.audit(dict(
        sources=[os.path.join(empty_root, "train.py")],
        command_globs=[os.path.join(empty_root, "runs", "**", "*.cmd")],
        config_globs=[],
        run_root=os.path.join(empty_root, "runs"),
        max_scan_mb=5,
    ))
    check("空脚本 run 不完整", empty["records_complete"], False)
    check("空脚本 run 状态", row(empty, "--head")["state"], "unrecorded")

    # 11c 日志正文里的 --flag 不能单独当命令证据
    check("日志正文不是证据",
          af.is_command_evidence("run.log", "skipped --head because OOM\n", "--"), False)
    check("日志命令行是证据",
          af.is_command_evidence("run.log", "python train.py --head\n", "--"), True)
    log_root = tempfile.mkdtemp()
    train_source(os.path.join(log_root, "train.py"), 'ap.add_argument("--head", default="")\n')
    write(os.path.join(log_root, "runs", "run1", "run.log"), "skipped --head because OOM\n")
    log_only = af.audit(dict(
        sources=[os.path.join(log_root, "train.py")],
        command_globs=[os.path.join(log_root, "runs", "**", "*.log")],
        config_globs=[],
        run_root=os.path.join(log_root, "runs"),
        max_scan_mb=5,
    ))
    check("日志正文不算用过", row(log_only, "--head")["used"], False)
    check("日志正文状态", row(log_only, "--head")["state"], "unrecorded")

    # 12 有命令证据的 run 才允许确认
    complete_root = tempfile.mkdtemp()
    train_source(os.path.join(complete_root, "train.py"))
    write(os.path.join(complete_root, "runs", "run1", "a.cmd"), "--lr 0.1\n")
    complete = af.audit(dict(
        sources=[os.path.join(complete_root, "train.py")],
        command_globs=[os.path.join(complete_root, "runs", "**", "*.cmd")],
        config_globs=[],
        run_root=os.path.join(complete_root, "runs"),
        max_scan_mb=5,
    ))
    check("完整命令记录可确认", row(complete, "--unused-a")["state"], "confirmed_unused")

    # 13 调整后覆盖率需要的「不适用」清单会透传
    check("不适用清单透传",
          af.audit(dict(cfg, not_applicable=["unused-a"]))["not_applicable"], ["unused-a"])

    # 14 方向标记：只碰过默认值 / 单向扫描
    check("只碰过默认值", row(result, "--fixed")["default_only"], True)
    check("默认值两侧都试过", row(result, "--seed")["default_only"], False)
    check("仅上调", row(result, "--width")["one_sided"], "仅上调")
    check("仅下调", row(result, "--depth")["one_sided"], "仅下调")
    check("两侧都试过不标方向", row(result, "--seed")["one_sided"], None)
    check("布尔不标方向", row(result, "--amp")["one_sided"], None)
    check("choices 不标方向", row(result, "--loss")["one_sided"], None)
    check("默认值归一（bool）", af.default_state(
        dict(action="store_true", default=None), True), "off")
    check("默认值归一（store_false）", af.default_state(
        dict(action="store_false", default=True), True), "on")
    check("默认值归一（数值）", af.default_state(
        dict(default=1e-4, type_name="float"), False), "0.0001")

    ok = sum(1 for *_, passed in cases if passed)
    for label, got, want, passed in cases:
        print(f"{'OK ' if passed else 'FAIL'} {label:<28} got={got!r} want={want!r}")
    print(f"\n{ok}/{len(cases)} 通过")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
