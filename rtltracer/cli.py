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
from rtltracer.db import Db, DbError, open_db
from rtltracer import commands
from rtltracer import cone
from rtltracer.resolve import ResolveError

TOOL = "rtltracer"


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

    def loc(e, width=0):
        s = f"{e['file']}:{e['line']}" if e.get("file") and e.get("line") else ""
        return c.dim(f"{s:<{width}}") if width else c.dim(s)

    def plur(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    def field(k, v, hot=False):
        lab = (c.yellow if hot else c.dim)(f"{k:<12}")
        return f"      {lab}  {v}"

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
            for nxt in levels[i + 1:]:
                if nxt["depth"] < d:
                    break
                if nxt["depth"] == d:
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
            arrow = f"{c.cyan(e['source'])} {c.dim('→')} {c.cyan(e['target'])}"
            lines.append(f"  {c.bold(f'[{i}]')} {arrow}")
            lines.append(field("depth", e["depth"]))
            lines.append(field("via", e["kind"], hot=e.get("control")))
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
                lines.append(f"    {c.dim('│ via ' + e['kind'])}  {loc(e)}")
                if e.get("statement"):
                    lines.append(f"    {c.dim('│ ' + e['statement'])}")
                lines.append(f"  {c.cyan(e['target'])}")
        else:
            lines.append(c.yellow("no path found"))
        lines.append("")
    lines.append(c.dim(f"  [{summary.get('_ms', 0)} ms]"))
    return "\n".join(lines) + "\n"


def _resolve_opt(v: str | None, default=""):
    return v if v is not None else default


def main():
    p = argparse.ArgumentParser(prog=TOOL, description="Signal trace over an rtl-designdb design database.")
    jp = argparse.ArgumentParser(add_help=False)
    jp.add_argument("--json", action="store_true", help="Emit JSON envelope instead of human text")
    sub = p.add_subparsers(dest="command", required=True)

    # info
    pi = sub.add_parser("info", parents=[jp], help="Database seal and source status")
    pi.add_argument("db", type=Path)

    # tree
    pt = sub.add_parser("tree", parents=[jp], help="Hierarchy levels")
    pt.add_argument("db", type=Path)
    pt.add_argument("scope", nargs="?", default=None, help="Start scope")
    pt.add_argument("--depth", type=int, default=3, help="Levels deep; 0 = all")
    pt.add_argument("--limit", type=int, default=0, help="Max levels shown; 0 = all")

    # find
    pf = sub.add_parser("find", parents=[jp], help="Find by name")
    pf.add_argument("db", type=Path)
    pf.add_argument("pattern", help="Name glob (use * and ?)")
    pf.add_argument("--instances", action="store_true")
    pf.add_argument("--modules", action="store_true")
    pf.add_argument("--limit", type=int, default=200, help="Max hits; 0 = all")

    # trace
    ptr = sub.add_parser("trace", parents=[jp], help="One hop: who drives / reads a signal")
    ptr.add_argument("db", type=Path)
    ptr.add_argument("signal", help="Hierarchical path, e.g. top.u_core.result")
    ptr.add_argument("--load", action="store_true", help="Show loads instead of drivers")
    ptr.add_argument("--ctl", action="store_true", help="Include control (gating) arcs")
    ptr.add_argument("--top", default="", help="Top module name if several")

    # fanin
    pfi = sub.add_parser("fanin", parents=[jp], help="Everything driving a signal, transitively")
    pfi.add_argument("db", type=Path)
    pfi.add_argument("signal")
    pfi.add_argument("--depth", type=int, default=4, help="Max hops; 0 = unbounded")
    pfi.add_argument("--comb", action="store_true", help="Stop at state elements")
    pfi.add_argument("--through-latch", action="store_true", help="Under --comb, cross latches anyway")
    pfi.add_argument("--no-ctl", action="store_true", help="Exclude control arcs")
    pfi.add_argument("--follow-ctl", action="store_true", help="Follow control arcs transitively")
    pfi.add_argument("--ctl-depth", type=int, default=None, metavar="N",
                     help="Follow control arcs, at most N levels")
    pfi.add_argument("--top", default="")

    # fanout
    pfo = sub.add_parser("fanout", parents=[jp], help="Everything a signal drives, transitively")
    pfo.add_argument("db", type=Path)
    pfo.add_argument("signal")
    pfo.add_argument("--depth", type=int, default=4)
    pfo.add_argument("--comb", action="store_true")
    pfo.add_argument("--through-latch", action="store_true")
    pfo.add_argument("--no-ctl", action="store_true")
    pfo.add_argument("--follow-ctl", action="store_true")
    pfo.add_argument("--ctl-depth", type=int, default=None, metavar="N",
                     help="Follow control arcs, at most N levels")
    pfo.add_argument("--top", default="")

    # path
    pp = sub.add_parser("path", parents=[jp], help="Shortest route between two signals")
    pp.add_argument("db", type=Path)
    pp.add_argument("from_signal", help="Start signal")
    pp.add_argument("to_signal", help="Target signal")
    pp.add_argument("--depth", type=int, default=0, help="Max hops; 0 = unbounded")
    pp.add_argument("--comb", action="store_true")
    pp.add_argument("--through-latch", action="store_true")
    pp.add_argument("--no-ctl", action="store_true")
    pp.add_argument("--follow-ctl", action="store_true")
    pp.add_argument("--ctl-depth", type=int, default=None, metavar="N",
                     help="Follow control arcs, at most N levels")
    pp.add_argument("--top", default="")

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
