# RTLTracer

查询 [RTLDebugDBKit](https://github.com/neveltyc/RTLDebugDBKit) 导出的设计数据库（schema v20），回答信号级问题：谁驱动它、谁读它、它依赖什么、从哪里到哪。

## 安装

```bash
pip install -e .
```

需要 Python 3.11+，无第三方依赖。

## 用前须知

先用 `rtl-designdb` 把 RTL 导成一个 SQLite 文件：

```bash
rtl-designdb -f filelist.f --top top -o design.db
```

之后的查询都不再读 RTL，只读这个库。

## 用法

```bash
rtltracer info design.db                  # 库是否可信、分析是否完整
rtltracer tree design.db                  # 设计由什么组成
rtltracer find design.db 'req*'           # 信号叫什么、在哪
rtltracer trace design.db top.alu.result   # 谁驱动它（--load 反过来）
rtltracer fanin design.db top.alu.result  # 它依赖什么，多层
rtltracer fanout design.db top.clk        # 谁依赖它，多层
rtltracer path design.db top.a top.out    # 两个信号之间有没有路
```

信号路径可以直接用波形里的写法，测试台层级会自动丢弃：

```bash
rtltracer trace design.db tb.dut.top.alu.result
```

默认输出给人看；加 `--json` 输出结构化结果，字段即上文各命令返回的内容。

## 常用选项

```bash
rtltracer fanin design.db top.q --depth 6      # 走多少层（0 = 不限制）
rtltracer fanin design.db top.q --comb         # 只看到寄存器/锁存器为止
rtltracer fanin design.db top.q --through-latch # --comb 时允许穿过锁存器
rtltracer trace design.db top.q --ctl          # 把条件信号也算作读取
rtltracer fanin design.db top.q --no-ctl       # 忽略条件信号
rtltracer fanin design.db top.q --follow-ctl   # 跟着条件信号继续追
rtltracer fanin design.db top.q --ctl-depth 2  # 条件信号只追 2 层
```

## 说明

工具只反映导出的内容，不做仿真判断；哪些驱动实际生效、某个值对不对，需要结合波形自己判断。

只对接当前 schema 版本（v20），不做前向兼容：库的版本对不上会直接报错，上游升级后用匹配的 `rtl-designdb` 重新导出即可。

## License

MIT
