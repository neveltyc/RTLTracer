"""RTLTracer — thin CLI over SQL queries against an RTLDebugDBKit v20 database.

Usage:  rtltracer <command> <db> [args] [--json]

Commands:
  info   DB                  Seal, sources, analysis status
  tree   DB [SCOPE]          Hierarchy levels
  find   DB PATTERN          Net / instance / module by name (glob)
  trace  DB SIGNAL           One hop: who drives it (--load: who reads it)
  fanin  DB SIGNAL           Everything driving it, transitively
  fanout DB SIGNAL           Everything it drives, transitively
  path   DB FROM TO          Shortest route between two signals

Run 'rtltracer <command> --help' for per-command options.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from rtltracer import __version__
from rtltracer.db import DbError, open_db
from rtltracer import commands
from rtltracer import cone
from rtltracer.resolve import ResolveError

TOOL = "rtltracer"

_HELP = """\
commands (each takes DB, the design database):
  info    DB              seal, sources, analysis status
  tree    DB [SCOPE]      hierarchy levels
  find    DB PATTERN      match nets by name glob
  trace   DB SIGNAL       one hop: who drives it
  fanin   DB SIGNAL       everything driving it, transitively
  fanout  DB SIGNAL       everything it drives, transitively
  path    DB FROM TO      shortest driver route between two signals

options by command:
  --json                        JSON instead of human text (any command)
  tree    --depth N (0=all)     --limit N (0=all)
  find    --instances|--modules match instances or modules instead of nets
          --limit N (0=all)
  trace   --load                readers instead of drivers
          --ctl                 include control (gating) arcs
  fanin / fanout / path:
          --depth N             max hops, 0 = unbounded
          --comb                stop at flops and latches
          --through-latch       with --comb, cross latches
          --no-ctl              drop control arcs
          --follow-ctl          follow control arcs transitively
          --ctl-depth N         follow control arcs, at most N levels
  --top NAME                    choose the top if several elaborated (trace + cones)

SIGNAL, FROM and TO are hierarchical paths (top.u_core.q); a leading testbench
scope is dropped. A trailing bit-select narrows fanin/fanout/path to those bits
-- q[17] or q[7:4] by declared index, q@[17] by flattened LSB offset. Add --json
to any command for the machine-readable envelope."""


class _Ink:
    """ANSI styling, but only for an interactive terminal (honours NO_COLOR).
    Piped or redirected output stays plain, so JSON and greps are untouched."""

    def __init__(self, stream):
        self.on = stream.isatty() and os.environ.get("NO_COLOR") is None

    def _p(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s): return self._p("1", s)
    def dim(self, s): return self._p("2", s)
    def red(self, s): return self._p("31", s)
    def green(self, s): return self._p("32", s)
    def yellow(self, s): return self._p("33", s)
    def cyan(self, s): return self._p("36", s)

    def state(self, s: str) -> str:
        good = {"resolved", "complete", "ok", "current"}
        warn = {"boundary_only", "partial", "hierarchy_only", "stale",
                "no_driver_found", "no_load_found"}
        return self.green(s) if s in good else self.yellow(s) if s in warn else self.red(s)


def _envelope(command: str, args: dict, status: str, data: dict, summary: dict,
              diagnostics: list, errors: list) -> str:
    body = {
        "tool": TOOL,
        "version": __version__,
        "status": status,
        "command": {"name": command, "args": args},
        "data": data,
        "diagnostics": diagnostics,
        "errors": errors,
        "summary": summary,
    }
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def _result(name: str, args: dict, outcome, json_mode: bool):
    if isinstance(outcome, Exception):
        if json_mode:
            out = _envelope(name, args, "error", None, None, [],
                            [{"code": outcome.code, "message": str(outcome)}])
            sys.stdout.write(out)
        else:
            _emit_error(str(outcome))
        return 1
    data, summary, diagnostics = outcome["data"], outcome["summary"], outcome["diagnostics"]
    if json_mode:
        out = _envelope(name, args, "ok", data, summary, diagnostics, [])
        sys.stdout.write(out)
        return 0
    # Human text
    sys.stdout.write(_human(name, data, summary))
    e = _Ink(sys.stderr)
    for d in diagnostics:
        if d.get("severity") == "warning":
            print(f"{e.yellow('warning:')} {d['message']}", file=sys.stderr)
    return 0


def _emit_error(msg: str):
    e = _Ink(sys.stderr)
    print(f"{e.red('error:')} {msg}", file=sys.stderr)


def _human(name: str, data: dict, summary: dict) -> str:
    c = _Ink(sys.stdout)
    lines = []

    def loc(e):
        s = f"{e['file']}:{e['line']}" if e.get("file") and e.get("line") else ""
        return c.dim(s)

    def plur(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    def field(k, v, hot=False):
        lab = (c.yellow if hot else c.dim)(f"{k:<12}")
        return f"      {lab}  {v}"

    def winbits(w):   # a bit window [lo] / [lo:hi] as LSB offsets, "" for whole
        if not w:
            return ""
        return f"[{w[0]}]" if w[0] == w[1] else f"[{w[0]}:{w[1]}]"

    if name == "info":
        a = data["analysis"]
        prod = f"{data['producer']['tool']} {data['producer']['tool_version']}"
        lines.append(f"{c.bold('Database:')} {data['path']}")
        lines.append(f"{c.bold('Schema:  ')} v{data['schema_version']}  {c.dim('(' + prod + ')')}")
        lines.append(f"{c.bold('Top:     ')} {c.cyan(data['top'] or '(none)')}")
        lines.append(f"{c.bold('Analysis:')} {c.state(a['status'])}")
        for k in ("error_count", "empty_procedure_count", "duplicate_path_count", "truncated_call_count", "unanalysed_inst_count"):
            if a[k] > 0:
                lines.append(c.yellow(f"  short: {a[k]} {k.replace('_', ' ')}"))
        for k in ("unresolved_count", "checker_inst_count"):
            if a[k] > 0:
                lines.append(c.dim(f"  declined: {a[k]} {k.replace('_', ' ')}"))
        if a["recursion_count"] > 0:
            lines.append(c.yellow(f"  truncated: {plur(a['recursion_count'], 'recursive instance')}"))
        stale, missing = summary["sources_stale"], summary["sources_missing"]
        tail = c.yellow(f", {stale} stale, {missing} missing") if stale + missing > 0 else ""
        lines.append(f"{c.bold('Sources: ')} {plur(summary['sources'], 'file')}{tail}")
        for s in data["sources"]:
            if s["state"] in ("stale", "missing"):
                lines.append(f"  {c.state(s['state'])}: {s['path']}")
        lines.append("")
    elif name == "tree":
        levels = data["levels"]
        # A node is its parent's last child when the next line at its own depth
        # never comes before the walk pops to a shallower one.
        last = [True] * len(levels)
        for i, lv in enumerate(levels):
            d = lv["depth"]
            for j in range(i + 1, len(levels)):
                nd = levels[j]["depth"]
                if nd < d:
                    break
                if nd == d:
                    last[i] = False
                    break
        last_at = {}
        for i, lv in enumerate(levels):
            d = lv["depth"]
            last_at[d] = last[i]
            if d == 0:
                stem = ""
            else:
                rail = "".join("    " if last_at.get(k, True) else "│   " for k in range(1, d))
                stem = c.dim(rail + ("└── " if last[i] else "├── "))
            what = c.dim(lv["module"] or lv["kind"])
            nets = c.dim("  " + plur(lv["nets"], "net")) if lv["nets"] else ""
            lines.append(f"{stem}{c.cyan(lv['path'].split('.')[-1])}  {what}{nets}")
        if summary["truncated"]:
            lines.append(c.dim(f"\ntruncated: {summary['shown']}/{summary['levels']} levels"))
        if summary["depth_truncated"]:
            lines.append(c.dim(f"\nstopped at depth {data['max_depth']}"))
        lines.append("")
    elif name == "find":
        if not data["hits"]:
            lines.append(c.dim(f"no {data['kind']} matches '{data['pattern']}'"))
        for h in data["hits"]:
            detail = h.get("detail")
            lines.append(f"  {c.cyan(h['path'])}{'  ' + c.dim(detail) if detail else ''}")
        if summary["truncated"]:
            lines.append(c.dim(f"\ntruncated: first {summary['shown']}; raise --limit for more"))
        lines.append("")
    elif name == "trace":
        noun = "driver" if data["direction"] == "driver" else "reader"
        n = len(data["hops"])
        bits = f"{data['bits']} " if data.get("bits") else ""
        lines.append(f"{c.bold('signal')} {c.cyan(data['signal'])}  {bits}{c.dim('[' + str(data['width']) + ' bits]')}")
        count = c.bold(f"{n} {noun}" + ("" if n == 1 else "s"))
        st = data["status"]
        if st == "boundary_only":
            lines.append(f"{count}{c.yellow(', only at the module boundary')}")
        elif st in ("no_driver_found", "no_load_found"):
            lines.append(c.yellow(f"no {noun} found"))
        else:
            lines.append(count)
        lines.append("")
        for i, h in enumerate(data["hops"], 1):
            if i > 1:
                lines.append("")
            text = h["statement"] or c.dim(f"<{h['raw_kind']}>")
            lines.append(f"  {c.bold(f'[{i}]')} {text}")
            lines.append(field("kind", h["kind"]))
            if h.get("file") and h.get("line"):
                lines.append(field("location", c.dim(f"{h['file']}:{h['line']}")))
            if h["source"] not in ("current", "read"):
                lines.append(field("source", c.yellow(h["source"])))
            if h.get("timing"):
                ev = ", ".join(f"{e['edge']} {e['signal']}" for e in h["timing"]["events"])
                body = f"{h['timing']['proc_kind']} @({ev})" if ev else h["timing"]["proc_kind"]
                lines.append(field("timing block", body))
            for g in h.get("gates", []):
                info = g["kind"]
                if g.get("sense"):
                    info += f" ({g['sense']})"
                if g.get("labels"):
                    info += f" = {g['labels']}"
                if g.get("reads"):
                    info += f" [{', '.join(g['reads'])}]"
                if g.get("iteration"):
                    info += f" iter {g['iteration']}"
                lines.append(field("condition", info, hot=True))
            if h.get("unreachable"):
                lines.append(field("unreachable", c.red("constant condition rules this out"), hot=True))
            if h.get("call_chain"):
                lines.append(field("via", c.dim(" → ".join(h["call_chain"]))))
            arrow = "from" if data["direction"] == "driver" else "to"
            for s in h.get("signals", []):
                if data.get("granularity") == "bit":
                    if s in h.get("widened_far", []):
                        lines.append(field(arrow, c.cyan(s) + c.dim("[*]") + "  " + c.yellow("precision widened"), hot=True))
                    else:
                        w = h.get("far_windows", {}).get(s)
                        lines.append(field(arrow, c.cyan(s + (winbits(w) if w else ""))))
                else:
                    lines.append(field(arrow, c.cyan(s)))
            for s in h.get("unresolved", []):
                lines.append(field("unresolved", c.cyan(s), hot=True))
        for i, p in enumerate(data.get("procedures", [])):
            lines.append(c.dim(f"\n  procedure #{i + 1} ({p['proc_kind']}) writes {len(p['writes'])} times:"))
            for w in p["writes"]:
                bits = f"   bits {w.get('bits')}" if w.get("bits") else ""
                uncond = c.dim("   [ungated]") if w.get("unconditional") else ""
                chain = c.dim(f"   via {' → '.join(w.get('call_chain', []))}") if w.get("call_chain") else ""
                ln = f"{w.get('line') or '':>5}"
                lines.append(f"    {c.dim(ln)}  {w.get('statement') or '<assignment>'}{bits}{uncond}{chain}")
        lines.append("")
    elif name in ("fanin", "fanout"):
        lines.append(f"{c.bold(name)} of {c.cyan(data['start'])}")
        parts = [plur(summary["nodes"], "node"), plur(summary["edges"], "edge")]
        if data.get("comb"):
            parts.append("combinational")
        if summary.get("control_edges"):
            parts.append(plur(summary["control_edges"], "condition"))
        lines.append(c.dim(", ".join(parts)))
        for i, e in enumerate(data.get("edges", []), 1):
            lines.append("")
            src = e["source"] + winbits(e.get("source_window"))
            tgt = e["target"] + winbits(e.get("target_window"))
            arrow = f"{c.cyan(src)} {c.dim('→')} {c.cyan(tgt)}"
            lines.append(f"  {c.bold(f'[{i}]')} {arrow}")
            lines.append(field("depth", e["depth"]))
            lines.append(field("via", e["kind"], hot=e.get("control")))
            if e.get("widened"):
                lines.append(field("note", "precision widened: bit correspondence unavailable", hot=True))
            if e.get("ends_at_state"):
                lines.append(field("note", "stops at a state element", hot=True))
            if e.get("file") and e.get("line"):
                lines.append(field("location", loc(e)))
            if e.get("statement"):
                lines.append(field("code", e["statement"]))
        lines.append("")
    elif name == "path":
        lines.append(f"{c.bold('path')} {c.cyan(data['from'])} {c.dim('→')} {c.cyan(data['to'])}")
        if data["found"]:
            lines.append(c.green(f"found, {plur(data['length'], 'hop')}"))
            lines.append("")
            lines.append(f"  {c.cyan(data['from'])}")
            for e in data.get("edges", []):
                lines.append(f"    {c.dim('│ via ' + (e['kind'] or '?'))}  {loc(e)}")
                if e.get("statement"):
                    lines.append(f"    {c.dim('│ ' + e['statement'])}")
                if e.get("widened"):
                    lines.append(f"    {c.dim('│')} {c.yellow('precision widened')}")
                lines.append(f"  {c.cyan(e['target'] + winbits(e.get('target_window')))}")
        else:
            lines.append(c.yellow("no path found"))
        lines.append("")
    lines.append(c.dim(f"  [{summary.get('_ms', 0)} ms]"))
    return "\n".join(lines) + "\n"


def _resolve_opt(v: str | None, default=""):
    return v if v is not None else default


def main():
    p = argparse.ArgumentParser(
        prog=TOOL, formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="%(prog)s [-h] [-V] COMMAND [ARGS]",
        description="Signal trace, driver and load analysis over an rtl-designdb "
                    "design database (schema v20).",
        epilog=_HELP)
    p.add_argument("-V", "--version", action="version", version=f"{TOOL} {__version__}")

    # -h anywhere, including after a subcommand, shows this one help.
    class _Help(argparse.Action):
        def __init__(self, option_strings, dest, **kw):
            super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kw)

        def __call__(self, *_a, **_k):
            p.print_help()
            p.exit()

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-h", "--help", action=_Help, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true")

    walk = argparse.ArgumentParser(add_help=False)  # fanin / fanout / path
    walk.add_argument("--comb", action="store_true")
    walk.add_argument("--through-latch", action="store_true")
    walk.add_argument("--no-ctl", action="store_true")
    walk.add_argument("--follow-ctl", action="store_true")
    walk.add_argument("--ctl-depth", type=int, default=None, metavar="N")
    walk.add_argument("--top", default="", metavar="NAME")

    # The one help text lives in _HELP; the auto per-command listing is hidden.
    sub = p.add_subparsers(dest="command", metavar="COMMAND", required=True,
                           help=argparse.SUPPRESS)

    def cmd(name, *parents):
        return sub.add_parser(name, parents=[common, *parents], add_help=False)

    pi = cmd("info")
    pi.add_argument("db", metavar="DB", type=Path)

    pt = cmd("tree")
    pt.add_argument("db", metavar="DB", type=Path)
    pt.add_argument("scope", nargs="?", default=None, metavar="SCOPE")
    pt.add_argument("--depth", type=int, default=3, metavar="N")
    pt.add_argument("--limit", type=int, default=0, metavar="N")

    pf = cmd("find")
    pf.add_argument("db", metavar="DB", type=Path)
    pf.add_argument("pattern", metavar="PATTERN")
    what = pf.add_mutually_exclusive_group()
    what.add_argument("--instances", action="store_true")
    what.add_argument("--modules", action="store_true")
    pf.add_argument("--limit", type=int, default=200, metavar="N")

    ptr = cmd("trace")
    ptr.add_argument("db", metavar="DB", type=Path)
    ptr.add_argument("signal", metavar="SIGNAL")
    ptr.add_argument("--load", action="store_true")
    ptr.add_argument("--ctl", action="store_true")
    ptr.add_argument("--top", default="", metavar="NAME")

    pfi = cmd("fanin", walk)
    pfi.add_argument("db", metavar="DB", type=Path)
    pfi.add_argument("signal", metavar="SIGNAL")
    pfi.add_argument("--depth", type=int, default=4, metavar="N")

    pfo = cmd("fanout", walk)
    pfo.add_argument("db", metavar="DB", type=Path)
    pfo.add_argument("signal", metavar="SIGNAL")
    pfo.add_argument("--depth", type=int, default=4, metavar="N")

    pp = cmd("path", walk)
    pp.add_argument("db", metavar="DB", type=Path)
    pp.add_argument("from_signal", metavar="FROM")
    pp.add_argument("to_signal", metavar="TO")
    pp.add_argument("--depth", type=int, default=0, metavar="N")

    args = p.parse_args()
    try:
        db = open_db(str(args.db))
    except DbError as e:
        if args.json:
            sys.stdout.write(_envelope(args.command, {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}, "error", None, None, [],
                                       [{"code": e.code, "message": e.message}]))
        else:
            _emit_error(e.message)
        return 1

    cmd = args.command
    top = _resolve_opt(getattr(args, "top", None))
    json_mode = args.json

    try:
        _t0 = time.perf_counter()
        if cmd == "info":
            outcome = commands.info(db)
        elif cmd == "tree":
            outcome = commands.tree(db, args.scope, args.depth, args.limit)
        elif cmd == "find":
            kind = "module" if args.modules else ("instance" if args.instances else "net")
            outcome = commands.find(db, args.pattern, kind, args.limit)
        elif cmd == "trace":
            outcome = commands.trace(db, args.signal, args.load, args.ctl, top)
        elif cmd == "fanin":
            outcome = cone._walk(db.conn.cursor(), db, args.signal, "driver",
                                 args.depth, args.no_ctl, args.comb, args.through_latch,
                                 args.follow_ctl, args.ctl_depth, top)
        elif cmd == "fanout":
            outcome = cone._walk(db.conn.cursor(), db, args.signal, "load",
                                 args.depth, args.no_ctl, args.comb, args.through_latch,
                                 args.follow_ctl, args.ctl_depth, top)
        elif cmd == "path":
            outcome = cone.path(db, args.from_signal, args.to_signal,
                                args.depth, args.no_ctl, args.comb, args.through_latch,
                                args.follow_ctl, args.ctl_depth, top)
        else:
            raise DbError("BAD_COMMAND", f"unknown command: {cmd}")
        outcome["summary"]["_ms"] = int((time.perf_counter() - _t0) * 1000)
        rc = _result(cmd, {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}, outcome, json_mode)
    except (ResolveError, DbError) as e:
        # Convert to error envelope
        if hasattr(e, "code"):
            code = e.code
        else:
            code = "ERROR"
        if json_mode:
            sys.stdout.write(_envelope(cmd, {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}, "error", None, None, [],
                                       [{"code": code, "message": str(e)}]))
        else:
            _emit_error(str(e))
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
