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
import sys
import time
from pathlib import Path

from rtltracer import __version__
from rtltracer.db import Db, DbError, open_db
from rtltracer import commands
from rtltracer import cone
from rtltracer.resolve import ResolveError

TOOL = "rtltracer"


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
            print(f"error: {outcome}", file=sys.stderr)
        return 1
    data, summary, diagnostics = outcome["data"], outcome["summary"], outcome["diagnostics"]
    if json_mode:
        out = _envelope(name, args, "ok", data, summary, diagnostics, [])
        sys.stdout.write(out)
        return 0
    # Human text
    sys.stdout.write(_human(name, data, summary))
    diags = [d for d in diagnostics if d.get("severity") == "warning"]
    for d in diags:
        print(f"warning: {d['message']}", file=sys.stderr)
    return 0


def _human(name: str, data: dict, summary: dict) -> str:
    lines = []
    if name == "info":
        lines.append(f"Database: {data['path']}")
        a = data["analysis"]
        lines.append(f"Schema:   v{data['schema_version']}  ({data['producer']['tool']} {data['producer']['tool_version']})")
        lines.append(f"Top:      {data['top'] or '(none)'}")
        lines.append(f"Analysis: {a['status']}")
        for k in ("error_count", "empty_procedure_count", "duplicate_path_count", "truncated_call_count", "unanalysed_inst_count"):
            if a[k] > 0:
                lines.append(f"  short: {a[k]} {k.replace('_', ' ')}")
        for k in ("unresolved_count", "checker_inst_count"):
            if a[k] > 0:
                lines.append(f"  declined: {a[k]} {k.replace('_', ' ')}")
        if a["recursion_count"] > 0:
            lines.append(f"  truncated: {a['recursion_count']} recursive instance(s)")
        stale = summary["sources_stale"]
        missing = summary["sources_missing"]
        lines.append(f"Sources:  {summary['sources']} file(s){f', {stale} stale, {missing} missing' if stale + missing > 0 else ''}")
        for s in data["sources"]:
            if s["state"] in ("stale", "missing"):
                lines.append(f"  {s['state']}: {s['path']}")
        if summary["shown_sources"] < summary["sources"]:
            lines.append(f"  ({summary['shown_sources']} of {summary['sources']} listed)")
        lines.append("")
    elif name == "tree":
        for lv in data["levels"]:
            indent = "  " * lv["depth"]
            what = lv["module"] or f"({lv['kind']})"
            nets = f"  [{lv['nets']} net(s)]" if lv["nets"] else ""
            lines.append(f"{indent}{lv['path'].split('.')[-1]}  {what}{nets}")
        if summary["truncated"]:
            lines.append(f"\ntruncated: {summary['shown']}/{summary['levels']} levels")
        if summary["depth_truncated"]:
            lines.append(f"\nstopped at depth {data['max_depth']}")
        lines.append("")
    elif name == "find":
        if not data["hits"]:
            lines.append(f"no {data['kind']} matches '{data['pattern']}'")
        for h in data["hits"]:
            detail = h.get("detail")
            lines.append(f"  {h['path']}{'  ' + detail if detail else ''}")
        if summary["truncated"]:
            lines.append(f"\ntruncated: first {summary['shown']}; raise --limit for more")
        lines.append("")
    elif name == "trace":
        lines.append(f"Signal: {data['signal']}{'  ' + data.get('bits', '') if data.get('bits') else ''}  [{data['width']} bits]")
        lines.append(f"{data['direction']}s: {data['status']} ({len(data['hops'])} hop(s))")
        if summary.get("multiple_drivers"):
            lines.append("  ! drivers overlap")
        lines.append("")
        for h in data["hops"]:
            at = f"{h['file']}:{h['line']}" if h.get("file") and h.get("line") else ""
            text = h["statement"] or f"<{h['raw_kind']}>"
            lines.append(f"  {h['kind']:<18} {at:<22} {text}")
            if h["source"] != "current" and h["source"] != "read":
                lines.append(f"      source: {h['source']}")
            if h.get("timing"):
                ev = ", ".join(f"{e['edge']} {e['signal']}" for e in h["timing"]["events"])
                lines.append(f"      timing: {h['timing']['proc_kind']} @({ev})" if ev else f"      timing: {h['timing']['proc_kind']}")
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
                lines.append(f"      when: {info}")
            if h.get("unreachable"):
                lines.append("      unreachable: constant condition rules this out")
            if h.get("call_chain"):
                lines.append(f"      via: {' -> '.join(h['call_chain'])}")
            for s in h.get("signals", []):
                lines.append(f"      from: {s}" if data['direction'] == 'driver' else f"      to: {s}")
            for s in h.get("unresolved", []):
                lines.append(f"      {s}  (unresolved)")
        for i, p in enumerate(data.get("procedures", [])):
            lines.append(f"\n  procedure #{i + 1} ({p['proc_kind']}) writes {len(p['writes'])} times:")
            for w in p["writes"]:
                bits = f"   bits {w.get('bits')}" if w.get("bits") else ""
                uncond = "   [ungated]" if w.get("unconditional") else ""
                chain = f"   via {' -> '.join(w.get('call_chain', []))}" if w.get("call_chain") else ""
                lines.append(f"    {w.get('line') or '':>5}  {w.get('statement') or '<assignment>'}{bits}{uncond}{chain}")
        lines.append("")
    elif name in ("fanin", "fanout"):
        lines.append(f"{name} of {data['start']}")
        lines.append(f"{summary['nodes']} node(s), {summary['edges']} edge(s)")
        if data.get("comb"):
            lines[-1] += ", combinational"
        if summary.get("control_edges"):
            lines[-1] += f", {summary['control_edges']} of them conditions"
        lines.append("")
        for e in data.get("edges", []):
            at = f"{e['file']}:{e['line']}" if e.get("file") and e.get("line") else ""
            marks = []
            if e.get("control"): marks.append("condition")
            if e.get("ends_at_state"): marks.append("stops at state")
            note = f"  [{', '.join(marks)}]" if marks else ""
            lines.append(f"  {e['depth']:>2}  {e['source']} -> {e['target']}{note}")
            lines.append(f"      {e['kind']:<18} {at}")
        if summary.get("cut"):
            lines.append(f"\ntruncated: {summary['shown_edges']}/{summary['edges']} edges")
        lines.append("")
    elif name == "path":
        lines.append(f"path: {data['from']} -> {data['to']}")
        if data["found"]:
            lines.append(f"found, {data['length']} hop(s)")
            lines.append("")
            lines.append(f"  {data['from']}")
            for e in data.get("edges", []):
                at = f"{e['file']}:{e['line']}" if e.get("file") and e.get("line") else ""
                lines.append(f"    | {e['kind']} {at}")
                lines.append(f"  {e['target']}")
        else:
            lines.append("not found")
        lines.append("")
    lines.append(f"  [{summary.get('_ms', 0)} ms]")
    return "\n".join(lines)


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
    pi.add_argument("--limit", type=int, default=0, help="Sources to show; 0 = all")

    # tree
    pt = sub.add_parser("tree", parents=[jp], help="Hierarchy levels")
    pt.add_argument("db", type=Path)
    pt.add_argument("scope", nargs="?", default=None, help="Start scope")
    pt.add_argument("--depth", type=int, default=3, help="Levels shown; 0 = all")
    pt.add_argument("--limit", type=int, default=0)

    # find
    pf = sub.add_parser("find", parents=[jp], help="Find by name")
    pf.add_argument("db", type=Path)
    pf.add_argument("pattern", help="Name glob (use * and ?)")
    pf.add_argument("--instances", action="store_true")
    pf.add_argument("--modules", action="store_true")
    pf.add_argument("--limit", type=int, default=200)

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
            print(f"error: {e.message}", file=sys.stderr)
        return 1

    cmd = args.command
    top = _resolve_opt(getattr(args, "top", None))
    json_mode = args.json

    try:
        _t0 = time.perf_counter()
        if cmd == "info":
            outcome = commands.info(db, args.limit)
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
            print(f"error: {e}", file=sys.stderr)
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
