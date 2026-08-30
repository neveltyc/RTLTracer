# RTLTracer

查询 [RTLDebugDBKit](https://github.com/neveltyc/RTLDebugDBKit) 导出的设计数据库（schema v22），回答信号级问题：谁驱动它、谁读它、它依赖什么、从哪里到哪。

## 安装

```bash
pip install -e .
```

需要 Python 3.11+，无第三方依赖。

等价的单文件版本在 [`dist-merged-py`](https://github.com/neveltyc/RTLTracer/tree/dist-merged-py) 分支的 `rtltracer-v22.py`：

```bash
curl -fsSL https://raw.githubusercontent.com/neveltyc/RTLTracer/dist-merged-py/rtltracer-v22.py -o rtltracer.py
```

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

## 位级追踪

`fanin` / `fanout` / `path` 支持位选，只追那些 bit，用的是数据库里已有的位级依赖，不重新分析 RTL：
`trace` / `fanin` / `fanout` / `path` 支持位选，只追那些 bit，用的是数据库里已有的位级依赖，不重新分析 RTL：

```bash
rtltracer fanin design.db top.data[17]     # 只追喂给 bit 17 的东西
rtltracer path design.db top.a[3] top.y    # 从 a 的 bit 3 找路
```

位选一路按精确对应映射到对端（`data[17] ← tmp[5] ← src[5]`）；遇到算术等无法逐位对应处，精度扩大为整条 net，并在该边标 `precision widened`。

位选写声明下标 `[hi:lo]`；对 struct、多维打包数组等没有单一声明范围的对象，用 `@[hi:lo]` 直接给 flattened LSB 偏移（普通 `[N:0]` 向量两者相同）。不带位选就是整条 net。

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

## 输出信息解读

终端输出带颜色，含义如下：

```
青色    信号或实例路径
→       信号流向
暗色    源码位置，形如 文件:行
黄色    条件门控
```

管道、重定向、或设了 `NO_COLOR` 时，输出自动转成纯文本。

`tree` 用 `├──` `└──` `│` 画层次，末尾是模块名和网络数。

```
top              top       12 nets
├── u_alu        alu        8 nets
└── u_regfile    regfile   20 nets
```

`trace` 只看一跳，也就是谁直接驱动这个信号。
每条驱动带序号，字段都带标签。

```
signal top.u_sub.dout  [8 bits]        # 目标信号和位宽
2 drivers                              # 找到几条驱动，正常时只报数量

  [1] if (rst) dout <= 8'h0;           # 第 1 条驱动，先给出源码
      kind          constant           # 驱动种类
      location      sample.sv:13        # 源码位置
      timing block  always_ff @(posedge clk)   # 所在时序块
      condition     if (then) [rst]     # 门控条件

  [2] else     dout <= din;
      kind          data
      location      sample.sv:14
      timing block  always_ff @(posedge clk)
      condition     if (else) [rst]
      from          top.u_sub.din       # 这条驱动的上游信号
```

`fanin` 顺着驱动一层层往回追，是多跳闭包。

`fanout` 是 `fanin` 的反向，顺着负载往下走。

```
fanin of top.q
4 nodes, 4 edges, 2 conditions         # 其中 2 条是条件

  [1] top.u_sub.dout → top.q           # 每条边一个序号
      depth     1                      # 距起点第几层
      via       connection             # 经哪种弧；控制弧是门控，显黄色
      location  sample.sv:45
      code      sub u_sub(... .dout(q) ...);   # 该位置的源码

  [2] top.u_sub.rst → top.u_sub.dout
      depth     2
      via       control                # 黄色，表示这是门控条件
      location  sample.sv:13
      code      if (rst) dout <= 8'h0;
```

末尾的 `[N ms]` 是这次查询的耗时。

## 说明

工具只反映导出的内容，不做仿真判断；哪些驱动实际生效、某个值对不对，需要结合波形自己判断。

只对接当前 schema 版本（v22），不做前向兼容：库的版本对不上会直接报错，上游升级后用匹配的 `rtl-designdb` 重新导出即可。

## License

MIT
