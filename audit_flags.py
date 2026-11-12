# -*- coding: utf-8 -*-
"""sweep_audit: 找出“代码里有、但历史记录里找不到”的配置开关。

三态结论：用过 / 无记录（待核实） / 确认没用
用法：改 CONFIG 后 python audit_flags.py
"""
import ast
import glob
import json
import os
import re
import sys

CONFIG = {
    # 定义参数的源码（argparse）
    "sources": ["train.py"],
    # 启动脚本 + 日志（只做 CLI 匹配）
    "command_globs": [
        "runs/**/*.cmd", "runs/**/*.bat", "runs/**/*.sh",
        "runs/**/*.ps1", "runs/**/*.log",
    ],
    # 配置文件（只做键值匹配）；默认只收 run 目录，避免 docker-compose 等无关文件污染
    "config_globs": [
        "runs/**/*.json", "runs/**/*.yaml", "runs/**/*.yml", "runs/**/*.toml",
    ],
    "max_scan_mb": 5,
    "prefix": "--",
    # 运行目录（用于判断“无记录”还是“确认没用”）
    "run_root": "runs",
    "exclude_dirs": [
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        "site-packages", "dist", "build",
    ],
    # 可选：已经判定“不适用”的参数名，用于同时报原始/调整后覆盖率
    "not_applicable": [],
}

CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml"}
COMMAND_EXTS = {".cmd", ".bat", ".sh", ".ps1"}
CLI_TOKEN_RE = re.compile(r"(?<![\w-])--[A-Za-z][\w-]*")
LAUNCH_RE = re.compile(
    r"(?i)(?:\b(?:python|py|torchrun|accelerate|train|make|bash|powershell|pwsh|cmd|call)\b"
    r"|\.(?:py|cmd|bat|sh|ps1)\b)"
)


def _clean(v):
    v = str(v).strip().rstrip(",}]").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def _unescape(v):
    return re.sub(r'\\(["\'\\])', r"\1", v)


def norm(v, prm=None):
    """按参数类型归一；字符串保留大小写和前导零。"""
    v = _clean(v)
    prm = prm or {}
    is_bool = bool(prm.get("is_bool")) or prm.get("action") in ("store_true", "store_false")
    type_name = (prm.get("type_name") or "").lower()

    if is_bool or type_name == "bool":
        low = v.lower()
        if low in ("true", "yes", "on", "1"):
            return "on"
        if low in ("false", "no", "off", "0"):
            return "off"
        return v

    if type_name in ("float", "int"):
        try:
            number = float(v)
        except ValueError:
            return v
        if type_name == "int" and number.is_integer():
            return str(int(number))
        return repr(number)

    return v


def _first_long_or_first(opts):
    long_opts = [o for o in opts if o.startswith("--")]
    return long_opts[0] if long_opts else opts[0]


def parse_args(sources, prefix):
    out = []
    for src in sources:
        if not os.path.exists(src):
            print(f"[warn] 源码不存在: {src}")
            continue
        tree = ast.parse(open(src, encoding="utf-8", errors="ignore").read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            opts = [a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                    and a.value.startswith(prefix)]
            if not opts:
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            action = None
            if "action" in kw and isinstance(kw["action"], ast.Constant):
                action = kw["action"].value

            dest = kw["dest"].value if "dest" in kw and isinstance(kw["dest"], ast.Constant) else None
            if dest is None:
                dest = _first_long_or_first(opts).lstrip("-").replace("-", "_")

            default, choices = None, None
            if "default" in kw:
                try:
                    default = ast.literal_eval(kw["default"])
                except Exception:
                    default = ast.unparse(kw["default"])
            elif action == "store_true":
                default = False
            elif action == "store_false":
                default = True
            if "choices" in kw:
                try:
                    choices = list(ast.literal_eval(kw["choices"]))
                except Exception:
                    choices = None

            # 显式 type 优先；没有 type 时从 default 推断。
            type_name = None
            if "type" in kw:
                try:
                    type_name = ast.unparse(kw["type"])
                except Exception:
                    type_name = None
            elif default is not None:
                if isinstance(default, bool):          # 必须先于 int
                    type_name = "bool"
                elif isinstance(default, int):
                    type_name = "int"
                elif isinstance(default, float):
                    type_name = "float"
                elif isinstance(default, str):
                    type_name = "str"
            out.append(dict(opts=opts, dest=dest, action=action, type_name=type_name,
                            default=default, choices=choices))
    return out


def _next_value(text):
    """取一个完整值单元；支持单双引号和转义。"""
    text = text.lstrip()
    if not text:
        return None
    quote = text[0]
    if quote in "\"'":
        chars, escaped, i = [], False, 1
        while i < len(text):
            ch = text[i]
            if escaped:
                chars.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                return "".join(chars)
            else:
                chars.append(ch)
            i += 1
        return "".join(chars)
    match = re.match(r"[^\s]+", text)
    return match.group(0) if match else None


def scan_cli(text, opts, prefix, is_bool, bool_on=True):
    vals = []
    for opt in opts:
        for match in re.finditer(re.escape(opt) + r"(?![\w-])", text):
            rest = text[match.end():match.end() + 200]
            # 值只在同一行找；续行已经在读文件时合并成空格。
            rest = rest.split("\n", 1)[0]
            body = rest[1:] if rest.startswith("=") else rest
            value = _next_value(body)
            if value is None or value.startswith(prefix) or value.startswith("#"):
                if is_bool:
                    vals.append("on" if bool_on else "off")
                continue
            if is_bool:
                vals.append("on" if bool_on else "off")
            else:
                vals.append(_clean(value))
    return vals


def scan_config_obj(obj, dest, acc):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == dest and value is not None and not isinstance(value, (dict, list)):
                acc.append(str(value))
            else:
                scan_config_obj(value, dest, acc)
    elif isinstance(obj, list):
        for value in obj:
            scan_config_obj(value, dest, acc)


QUOTED_VALUE = r'(?:"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'|([^\s#]+))'


def scan_config_text(text, dest, acc, ext):
    if ext not in (".yaml", ".yml", ".toml"):
        return
    if ext == ".toml":
        pattern = r"(?m)^[ \t]*" + re.escape(dest) + r"[ \t]*=[ \t]*" + QUOTED_VALUE
    else:
        pattern = r"(?m)^[ \t]*" + re.escape(dest) + r"[ \t]*:[ \t]*" + QUOTED_VALUE
    for match in re.finditer(pattern, text):
        value = next((g for g in match.groups() if g is not None), "")
        acc.append(_unescape(value))


def _strip_inline_hash(line):
    """去掉引号外的行内 # 注释；保留 "hello # world" 里的 #。"""
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "\"'":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line


def strip_comments(text, ext):
    """去掉整行/行内注释；引号里的 # 保留。"""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        upper = stripped.upper()
        if ext in (".cmd", ".bat") and (upper == "REM" or upper.startswith("REM ")
                                         or stripped.startswith("::")):
            continue
        if ext in (".sh", ".ps1") and stripped.startswith("#"):
            continue
        if ext in COMMAND_EXTS:
            line = _strip_inline_hash(line)
        lines.append(line)
    return "\n".join(lines)


def is_command_evidence(path, text, prefix):
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path).lower()
    if not text.strip():
        return False
    if ext in COMMAND_EXTS or base in ("makefile", "gnumakefile"):
        return bool(CLI_TOKEN_RE.search(text) or LAUNCH_RE.search(text))
    if ext == ".log":
        for line in text.splitlines():
            if CLI_TOKEN_RE.search(line) and LAUNCH_RE.search(line):
                return True
        return False
    return False


def filter_log_commands(text):
    """日志只保留同时含 CLI 选项和启动特征的行，避免正文里的 --flag 被当成用过。"""
    return "\n".join(
        line for line in text.splitlines()
        if CLI_TOKEN_RE.search(line) and LAUNCH_RE.search(line)
    )


def gather(globs, limit_mb, exclude_dirs=None):
    files, seen, skipped = [], set(), []
    excluded = {os.path.normcase(d) for d in (exclude_dirs or [])}
    for pattern in globs:
        for path in glob.glob(pattern, recursive=True):
            absolute = os.path.abspath(path)
            if absolute in seen or not os.path.isfile(absolute):
                continue
            seen.add(absolute)
            parts = {os.path.normcase(part) for part in absolute.split(os.sep)}
            if parts & excluded:
                continue
            if os.path.getsize(absolute) > limit_mb * 1024 * 1024:
                skipped.append(absolute)
                continue
            files.append(absolute)
    return files, skipped


def _is_under(path, directory):
    prefix = os.path.abspath(directory) + os.sep
    return os.path.abspath(path).startswith(prefix)


def default_state(prm, is_bool):
    """把默认值归一成与 uniq 相同的坐标系；布尔开关返回 on/off。"""
    action = prm.get("action")
    if is_bool:
        if action == "store_true":
            return "off"
        if action == "store_false":
            return "on"
        default = prm.get("default")
        if isinstance(default, bool):
            return "on" if default else "off"
        return None
    default = prm.get("default")
    if default is None:
        return None
    return norm(default, dict(prm, is_bool=is_bool))


def one_sided(values, default, prm, is_bool):
    """数值参数的所有取值都在默认值同侧时，标出扫描方向。"""
    if is_bool or prm.get("choices"):
        return None
    if (prm.get("type_name") or "").lower() not in ("float", "int"):
        return None
    if default is None or len(values) < 2:
        return None
    try:
        base = float(default)
        numbers = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if all(number > base for number in numbers):
        return "仅上调"
    if all(number < base for number in numbers):
        return "仅下调"
    return None


def audit(cfg=None):
    cfg = {**CONFIG, **(cfg or {})}
    prefix = cfg["prefix"]
    params = parse_args(cfg["sources"], prefix)

    all_cmd, cmd_skipped = gather(cfg["command_globs"], cfg["max_scan_mb"], cfg.get("exclude_dirs"))
    all_cfg, cfg_skipped = gather(cfg["config_globs"], cfg["max_scan_mb"], cfg.get("exclude_dirs"))
    cmd_files = [p for p in all_cmd if os.path.splitext(p)[1].lower() not in CONFIG_EXTS]
    cfg_files = [p for p in all_cfg if os.path.splitext(p)[1].lower() in CONFIG_EXTS]

    cmd_parts = []
    evidence_files = []
    for path in cmd_files:
        ext = os.path.splitext(path)[1].lower()
        text = open(path, encoding="utf-8", errors="ignore").read()
        text = strip_comments(text, ext)
        text = re.sub(r"\^\s*\r?\n", " ", text)      # Windows 续行
        text = re.sub(r"\\\s*\r?\n", " ", text)      # bash 续行
        if ext == ".log":
            text = filter_log_commands(text)
        cmd_parts.append(text)
        if is_command_evidence(path, text, prefix):
            evidence_files.append(path)
    cmd_text = "\n".join(cmd_parts)

    cfg_items = []
    for path in cfg_files:
        ext = os.path.splitext(path)[1].lower()
        text = open(path, encoding="utf-8", errors="ignore").read()
        if ext == ".json":
            try:
                cfg_items.append((ext, json.loads(text)))
            except Exception:
                continue
        else:
            cfg_items.append((ext, text))

    rows = []
    for prm in params:
        is_bool = prm["action"] in ("store_true", "store_false")
        raw = scan_cli(cmd_text, prm["opts"], prefix, is_bool,
                       bool_on=(prm["action"] != "store_false"))
        for ext, payload in cfg_items:
            if ext == ".json":
                scan_config_obj(payload, prm["dest"], raw)
            else:
                scan_config_text(payload, prm["dest"], raw, ext)
        uniq = sorted({norm(v, dict(prm, is_bool=is_bool)) for v in raw})
        default = default_state(dict(prm, is_bool=is_bool), is_bool)
        rows.append(dict(
            prm, raw=raw, uniq=uniq, is_bool=is_bool, used=bool(raw),
            default_norm=default,
            default_only=bool(uniq) and default is not None and uniq == [default],
            one_sided=one_sided(uniq, prm.get("default"), prm, is_bool),
        ))

    run_dirs = []
    if os.path.isdir(cfg["run_root"]):
        excluded = {os.path.normcase(d) for d in cfg.get("exclude_dirs", [])}
        for name in os.listdir(cfg["run_root"]):
            path = os.path.join(cfg["run_root"], name)
            if os.path.isdir(path) and os.path.normcase(name) not in excluded:
                run_dirs.append(path)

    uncovered, config_only = [], []
    for run_dir in run_dirs:
        has_command = any(_is_under(path, run_dir) for path in evidence_files)
        has_config = any(_is_under(path, run_dir) for path in cfg_files)
        if not has_command:
            uncovered.append(run_dir)
            if has_config:
                config_only.append(run_dir)

    skipped = cmd_skipped + cfg_skipped
    # 只有“每个 run 目录都有命令证据、且没有跳过文件”才允许说“确认没用”。
    records_complete = bool(run_dirs) and not uncovered and not skipped and bool(evidence_files)
    for row in rows:
        if row["used"]:
            row["state"] = "used"
        elif records_complete:
            row["state"] = "confirmed_unused"
        else:
            row["state"] = "unrecorded"
    return dict(rows=rows, cmd_files=cmd_files, cfg_files=cfg_files,
                evidence_files=evidence_files, skipped=skipped, run_dirs=run_dirs,
                uncovered_run_dirs=uncovered, config_only_run_dirs=config_only,
                records_complete=records_complete,
                not_applicable=list(cfg.get("not_applicable", [])))


def expected_kinds(row):
    if row["is_bool"]:
        return 1
    if row["choices"]:
        return min(3, len(row["choices"]))
    return 3


def main():
    result = audit()
    rows = result["rows"]
    print(f"{'参数':<24}{'默认':<14}{'记录数':>6}  取值")
    n_used = n_swept = 0
    for row in rows:
        kinds = expected_kinds(row)
        swept = len(row["uniq"]) >= kinds
        n_used += row["used"]
        n_swept += swept
        shown = ", ".join(row["uniq"][:6]) + (" ..." if len(row["uniq"]) > 6 else "")
        if row["state"] == "confirmed_unused":
            mark = "   <== 确认没用"
        elif not row["used"]:
            mark = "   <== 无记录，待核实"
        elif swept:
            mark = ""
        else:
            mark = f"   <== 只碰过 {len(row['uniq'])}/{kinds} 种取值"
        extra = f"  [{row['one_sided']}]" if row.get("one_sided") else ""
        print(f"{row['opts'][0]:<24}{str(row['default']):<14}{len(row['raw']):>6}  {shown}{mark}{extra}")

    print(f"\n参数总数 {len(rows)}；有记录 {n_used} 个；达到扫过标准 {n_swept} 个")
    print(f"原始覆盖率 {n_swept / max(len(rows), 1):.0%}  —— 通常偏低，但不是严格数学下限")
    na_names = {str(x).lstrip("-").replace("-", "_") for x in result["not_applicable"]}
    applicable = [r for r in rows
                  if r["opts"][0].lstrip("-").replace("-", "_") not in na_names]
    if na_names:
        n_swept_app = sum(1 for r in applicable
                          if len(r["uniq"]) >= expected_kinds(r))
        print(f"调整后覆盖率 {n_swept_app} / {len(applicable)} = {n_swept_app / max(len(applicable), 1):.0%}"
              f"  —— 已剔除 {len(na_names)} 个「不适用」参数")
    no_rec = [row["opts"][0] for row in rows if row["state"] == "unrecorded"]
    confirmed = [row["opts"][0] for row in rows if row["state"] == "confirmed_unused"]
    print(f"无记录 {len(no_rec)} 个（上限，待核实）：" + (", ".join(no_rec) if no_rec else "无"))
    if confirmed:
        print(f"确认没用 {len(confirmed)} 个：" + ", ".join(confirmed))
    default_only = [row["opts"][0] for row in rows if row.get("default_only")]
    one_side = [f"{row['opts'][0]}({row['one_sided']})"
                for row in rows if row.get("one_sided")]
    if default_only:
        print(f"只碰过默认值 {len(default_only)} 个：" + ", ".join(default_only))
    if one_side:
        print(f"单向扫描 {len(one_side)} 个：" + ", ".join(one_side))
    print(f"\n扫描：命令文件 {len(result['cmd_files'])} 个，配置文件 {len(result['cfg_files'])} 个，"
          f"运行目录 {len(result['run_dirs'])} 个，跳过超大文件 {len(result['skipped'])} 个")
    print(f"记录完整性：{'完整' if result['records_complete'] else '不完整'}"
          f"（无命令记录的 run 目录 {len(result['uncovered_run_dirs'])} 个，"
          f"其中只有配置的 {len(result['config_only_run_dirs'])} 个）")
    if result["run_dirs"] and not result["evidence_files"]:
        print("  [warn] 有运行目录但没有任何命令证据 → 全部结论只能是『无记录』")
    for path in result["cfg_files"][:10]:
        print(f"  [cfg] {path}")
    for path in result["skipped"][:5]:
        print(f"  [skip] {path}")
    print("\n判定口径：布尔显式出现即算试过；数值按显式/推断的 float/int 归一；字符串保留原样。")


if __name__ == "__main__":
    main()
