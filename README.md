# RTLTracer

Signal trace, driver and load analysis over an
[rtl-designdb](https://github.com/neveltyc/RTLDebugDBKit) v20 database.
The RTL is elaborated once by `rtl-designdb` into SQLite; this tool queries
that file. SQL is the contract: every query lives in
[rtltracer/sql/](rtltracer/sql/), and Python is a thin CLI that binds
parameters, names nets, and renders the answer.

It is not a simulator. It reports what the design is, never what it did at
some moment.

## Requires

* Python 3.11+ (standard library only: `sqlite3`, `argparse`, `hashlib`)
* A `design.db` exported by `rtl-designdb`, schema **v20**

## Use

```bash
python -m rtltracer info design.db
python -m rtltracer trace design.db top.u_core.u_alu.result
python -m rtltracer trace design.db top.u_core.status[3] --load
python -m rtltracer fanin design.db top.u_core.status --depth 6
python -m rtltracer fanin design.db top.u_core.status --comb
python -m rtltracer fanout design.db top.a --depth 4
python -m rtltracer path design.db top.a top.u_core.result
python -m rtltracer tree design.db --depth 2
python -m rtltracer find design.db 'req*'
```

Add `--json` for a machine-readable envelope (`tool`, `version`, `status`,
`command`, `data`, `diagnostics`, `errors`, `summary`). Without it the same
data renders for a terminal.

A path may carry testbench levels above the design root. Every suffix is
tried, longest first, so `tb.u_dut.top.a` and `top.a` name the same net and
the answer names what was discarded.

## Commands

| Command | Question | Reads |
|---|---|---|
| `info` | Can this database be trusted? | `v_db_info`, source SHA-256 check |
| `tree` | What is the design made of? | `v_tree_node`, `v_node_path`, `v_net` |
| `find` | Where does a name live? | `v_net`, `v_tree_node`, `module` |
| `trace` | Who drives / reads this signal? | `v_driver` / `v_load` + `v_stmt`, `v_branch`, `v_proc_event`, `v_call_site` |
| `fanin` | Everything it depends on, transitively | recursive closure over `v_driver` |
| `fanout` | Everything that depends on it | recursive closure over `v_load` |
| `path` | A route between two signals | BFS over `v_load` |

## Cone engines

`fanin`, `fanout` and `path` accept `--engine cte|bfs`.

* `cte` — the whole walk is one recursive SQL statement. Simple to read;
  SQLite materialises the composite view once, so large cones cost more.
* `bfs` — Python walks level by level, each level a batch point query that
  seeks the `net_dep_*` indexes. This is what the kit's schema documentation
  recommends for a closure that must stay index-fast.

Measured on a Windows desktop against real cores in this repo's
`.tools/` (not committed): `fanin --depth 4` on picorv32 was about 0.40 s
with `cte` and 0.23 s with `bfs`; `fanout --depth 3` on tinyriscv was
0.34 s vs 0.23 s. `path` on tinyriscv with `cte` did not finish in 40 s
(trail-guarded recursion explores too many simple paths), while `bfs`
returned the 22-hop route in well under a second, so `path` defaults to
`bfs`.

## Layout

```
rtltracer/
  cli.py          argparse, envelope, human render
  db.py           open + schema v20 gate
  resolve.py      bit-select split + declared-range mapping
  commands.py     info, tree, find, trace
  cone.py         fanin, fanout, path (cte + bfs)
  sql.py          SQL loader
  sql/            the queries themselves, one file per query
```

## Known limits

* Interface/modport path resolution and escaped-identifier segmentation are
  not implemented; basic tree walk + `v_node_path` covers regular hierarchy
  and generate blocks.
* Bit-select `[i]` / `[hi:lo]` is accepted on the queried signal, but the
  window is not propagated across hops; a cone narrows at the start only.
* `--comb` stops at state elements and crosses whole-width port ties
  (matching `rtlscanner`'s state-element propagation), but has no
  `--through-latch`.
* Source lines are quoted only when the file still hashes to the export;
  otherwise `source` says `stale` or `missing` and no text is claimed.

## Licence

MIT
