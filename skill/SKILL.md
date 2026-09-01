---
name: rtl-signal-trace
description: RTL static signal-tracing CLI for debug, review, and AI agents. Queries a design database exported by RTLDebugDBKit (SystemVerilog elaborated to SQLite) to answer signal-level questions — who drives a signal, who reads it, what it depends on transitively, and whether a path exists between two signals — with source file:line for every edge. Bit-level tracing supported. Use when the user has an RTL design (SystemVerilog) or a design .db and asks about drivers, loads, fan-in/fan-out cones, dependency paths, or where a signal comes from — triggers on driver/load queries, RTL debug, signal cone/dependency analysis, or a `.db` from rtl-designdb. Static structure only, no waveform values.
---

# rtltracer — agent skill

`rtltracer` answers signal-level structural questions about an RTL design from
the terminal: who drives a signal, who reads it, its transitive fan-in/fan-out
cone, and whether a path connects two signals — each answer carries the source
`file:line`. It is a thin consumer over a **design database** produced by
[RTLDebugDBKit](https://github.com/neveltyc/RTLDebugDBKit); it never reads RTL
itself. Eight commands, one SQLite input, no simulation. **Always pass `--json`
from an agent.** This file covers driving the tool from an agent — see the repo
README for the full reference.

It reflects only what was elaborated and exported: which drivers actually take
effect, or whether a value is correct, is a waveform question this tool does not
answer. Pair it with a waveform tool (e.g. rwave) when you need runtime values.

## Produce the database (once, with rtl-designdb)

`rtltracer` needs a `.db` exported by RTLDebugDBKit's `rtl-designdb`, which
elaborates SystemVerilog with slang and writes hierarchy, nets, port
connections, statements, control conditions, and static data dependencies into
SQLite. Grab the binary from
[RTLDebugDBKit releases](https://github.com/neveltyc/RTLDebugDBKit/releases/latest),
then:

```bash
rtl-designdb -f rtl.f --top top -o design.db   # elaborate a filelist into one .db
```

Common `rtl-designdb` args: `-f <filelist>` (VCS-style; also `+define+`,
`+incdir+`, `-I`, `-v`, `$VAR`), `--top <module>`, `--single-unit`, `-o
<file.db>`. Every later `rtltracer` query reads only this `.db` — no RTL, no
re-elaboration. Check it opened clean:

```bash
rtltracer --json info design.db     # or: sqlite3 -box design.db "SELECT * FROM v_db_info;"
```

`info`'s `analysis.status` is `complete` (fully exported), `partial` (usable but
has analysis gaps — trust results but expect some `unresolved`/`boundary`
outcomes), or `hierarchy_only` (only `tree`/`find` are meaningful). The `.db`
holds an **instance-level** design: two instances of one module are recorded
separately (`top.u_reg0.q` vs `top.u_reg1.q`), so drivers/loads are per-instance.

## Install

Python 3.11+, no third-party deps:

```bash
pip install -e .            # from a clone
```

Or the single-file bundle (no install):

```bash
curl -fsSL https://raw.githubusercontent.com/neveltyc/RTLTracer/dist-bundle/rtltracer-v22.py -o rtltracer.py
python rtltracer.py --json info design.db
```

## Pick the right command

```
User wants to know...
├─ "Is this DB trustworthy / complete?"
│   └─ info      <DB>              seal, producer, analysis status, sources
├─ "What is the design made of?"
│   └─ tree      <DB> [SCOPE]      instance hierarchy, module + net count per level
├─ "What is a signal called / where is it?"
│   └─ find      <DB> PATTERN      match nets by name glob (--instances / --modules)
├─ "Who drives this signal?" / "Who reads it?"
│   └─ trace     <DB> SIGNAL       one hop: direct drivers (--load: direct readers)
├─ "What does it depend on, transitively?"
│   └─ fanin     <DB> SIGNAL       full driver cone, multi-hop
├─ "What does it drive, transitively?"
│   └─ fanout    <DB> SIGNAL       full load cone, multi-hop
├─ "Is there a route from A to B?"
│   └─ path      <DB> FROM TO      shortest dependency path between two signals
└─ "Source paths broke after moving the DB?"
    └─ rebind    <DB> --src-root DIR   re-point src_file paths by content hash
```

`rebind` is a maintenance write (it edits `src_file.path` in place); the query
commands are read-only. If trace/fanin/fanout/path report stale or missing
source (a top-level `SOURCE_STALE` / `SOURCE_MISSING` diagnostic), point `rebind`
at the source tree(s) with one or more `--src-root DIR` to restore the index by
content SHA-256, then re-run. It supports `--json` and reports which files were
rebound, which were not, and whether all are `resolved`. It fixes a lost/wrong
index, not changed content — a file whose bytes changed will not match.

## Signal names & bit-select

- Paste the waveform-style path; a leading testbench scope is stripped
  automatically (`tb.dut.top.alu.result` resolves the same as `top.alu.result`).
- A pattern that resolves ambiguously errors with candidates — narrow it, or use
  `--top NAME` to fix the root. Bare name → whole net.
- **Bit-select** on `trace`/`fanin`/`fanout`/`path` traces only those bits, using
  the DB's existing bit-level dependencies (no re-analysis):
  - `sig[hi:lo]` — declared index range (`top.data[17]`, `top.a[3]`).
  - `sig@[hi:lo]` — flattened LSB offset, for structs / packed multi-dim arrays
    that have no single declared range. For a plain `[N:0]` vector the two forms
    coincide.
- Bit windows map exactly across each hop (`data[17] ← tmp[5] ← src[5]`); where a
  hop cannot map bit-for-bit (arithmetic, etc.) precision widens to the whole net
  and that edge is flagged `widened: true`.

## Common options

`fanin`/`fanout`/`path` share the walk options; `trace` takes only `--load`,
`--ctl`, `--top`.

| Option | Applies to | Effect |
|---|---|---|
| `--load` | trace | readers instead of drivers |
| `--ctl` | trace | count control (if/case) signals as reads too |
| `--depth N` | tree, fanin, fanout, path | hop limit; `0` = unbounded (defaults: tree 3, fanin/fanout 4, path 0) |
| `--comb` | fanin/fanout/path | stop at the first register/latch |
| `--through-latch` | fanin/fanout/path | with `--comb`, still cross latches |
| `--no-ctl` | fanin/fanout/path | ignore control signals |
| `--follow-ctl` | fanin/fanout/path | keep walking through control signals |
| `--ctl-depth N` | fanin/fanout/path | walk control signals only N hops |
| `--top NAME` | all trace/walk | root scope for name resolution |
| `--limit N` | tree, find | cap rows (find default 200; tree 0 = all) |

## Command quick reference

`--json` output is one envelope: top-level `status` (`ok`/`error`), `data`,
`summary`, `diagnostics`, `errors`. Parse `data`; read `summary` for counts.

| Command | Invocation | Useful JSON fields (under `data`) |
|---|---|---|
| `info` | `rtltracer --json info <DB>` | `schema_version`, `top`, `analysis.status`, `analysis.error_count`/`unresolved_count`, `sources[]`; summary `sources_stale`/`sources_missing` |
| `tree` | `rtltracer --json tree <DB> [SCOPE] [--depth N]` | `root`, `levels[].path`/`.module`/`.kind`/`.depth`/`.nets`/`.children`; summary `depth_truncated` |
| `find` | `rtltracer --json find <DB> PATTERN [--instances\|--modules]` | `kind`, `hits[].path`/`.what`/`.detail`; summary `truncated` |
| `trace` | `rtltracer --json trace <DB> SIGNAL [--load] [--ctl]` | `signal`, `direction`, `status`, `width`, `hops[].kind`/`.location`/`.timing`/`.gates`/`.signals[]` (the upstream/downstream nets) |
| `fanin` | `rtltracer --json fanin <DB> SIGNAL [--depth N]` | `start`, `nodes[]`, `edges[].source`/`.target`/`.kind`/`.depth`/`.control`/`.file`/`.line`/`.statement`; summary `control_edges`/`stopped_at_state` |
| `fanout` | `rtltracer --json fanout <DB> SIGNAL [--depth N]` | same shape as `fanin` |
| `path` | `rtltracer --json path <DB> FROM TO` | `found` (bool), `length`, `reason` (`bit_precision_lost` when a bit path was lost), `nodes[]`, `edges[]` |
| `rebind` | `rtltracer --json rebind <DB> --src-root DIR [--src-root DIR ...]` | `files[].basename`/`.rebound`/`.old_path`/`.new_path`; summary `matched`/`rebound`/`unmatched`/`resolved` |

Source-availability signal: trace/fanin/fanout/path emit a `diagnostics` warning
(`SOURCE_STALE` / `SOURCE_MISSING`) when referenced source no longer matches the
recorded digest; results are still produced but non-current source shows
`file:line` only, no quoted text. `rebind` restores the index (see above).

`trace.status` / `fanin`/`fanout` edges tell you where the walk ended:

- `trace` `status`: `resolved`, `boundary_only` (driver/load is outside the
  traced hierarchy — the honest end of the line, not a failure), `no_driver_found`
  / `no_load_found`.
- Cone edges: `kind == "control"` is a gating (if/case) edge; `ends_at_state`
  marks a hop stopped at a register/latch; `boundary` marks a module-boundary
  connection.

## Workflow patterns

(all assume `--json`)

### First contact with a design DB
```
1. info                              is it complete? which top? stale sources?
2. tree                              top-level instances and net counts
3. tree <block> --depth 2            what is inside one block
4. find '<suspect>*'                 pin down the exact signal path
```

### "Who / what produces this signal?"
```
1. trace  top.q                      the direct driver(s), with file:line + gating condition
2. fanin  top.q --depth 4            walk the whole driver cone
3. fanin  top.q --comb               only as far as the first flop (combinational cone)
4. trace  top.q --load               flip it: who reads q
```

### Follow a specific bit
```
1. fanin  top.data[17]               only what feeds bit 17
2. path   top.a[3] top.y             does a[3] reach y, and how
# watch for widened:true edges — precision fell back to the whole net there
```

### Trace across a clocked boundary
```
1. fanin  top.q                      default walk crosses flops, tags ends_at_state
2. fanin  top.q --comb               combinational-only; stops at each register
3. fanin  top.q --follow-ctl         also chase the if/case control signals
```

### Path / connectivity check
```
1. path top.a top.out                found:false ⇒ no static route within --depth
2. path top.a top.out --depth 0      unbounded, if a bounded search came up empty
```

## Agent-side gotchas

- **`--json` everywhere.** Text mode is colored and meant for humans (auto-plain
  under pipes / `NO_COLOR`); never parse it. Pass `--json` on every call.
- **Check `status` first.** Envelope `status:"error"` puts the reason in
  `errors[]` (`code`, `message`) and exits non-zero. On success, read the
  command's own `status`/`found` before trusting the list.
- **`boundary_only` / `no_driver_found` are answers, not retries.** They mean the
  DB genuinely has no further driver in the traced hierarchy — don't rerun with
  different flags expecting a different fact.
- **Truncation.** `find`/`tree` cap rows; `summary.truncated` (or
  `depth_truncated`) says so. Don't just raise `--limit` — narrow with a tighter
  `SCOPE`/pattern or a smaller `--depth`.
- **Bit-select refusal.** A struct field or packed-array element with no single
  declared range needs `@[hi:lo]` (flattened offset), not `[hi:lo]`.
- **Schema is pinned.** This build reads schema **v22** only; a mismatched `.db`
  errors outright. Re-export with a matching `rtl-designdb` — there is no forward
  compat to work around.
