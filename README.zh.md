# dsh-flymemory

[English](README.md) | 中文

给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 加长期记忆。

这是一个 DSH 插件（bundle）：把 [FlyMemory](https://github.com/aujurd22/flymemory)
当作本地 MCP 服务跑起来，接进 harness。装完之后模型手里多出 15 个记忆工具，另外还有
两个钩子在背后干活——你干活的过程它自己记，下次用得上就自己翻出来。省掉每次开会话都
重新交代一遍背景的功夫。

所有东西都在你自己机器上：服务监听 `127.0.0.1:8791`，记忆就是 DSH home 底下的一个
文件，没有任何数据发到外面。

```
DSH 会话 ──┬── mcp__flymemory__*   模型可调的 15 个工具
           └── 钩子                  回合前召回，回合后记录
                     │
                     ▼
           FlyMemory 服务  127.0.0.1:8791
                     │
                     ▼
           $DSH_HOME/flymemory-data/flymemory_v3.pkl
```

## 拿到什么

15 个工具，调用时都带 `mcp__flymemory__` 前缀：

| 工具 | 干什么用的 |
|---|---|
| `flymemory_remember` | 存一条决定或发现。近似重复会自动合并。`compartment` 按主题分区；`state_key`/`state_value` 记「某个东西当前是什么」，旧值自动退场。 |
| `flymemory_recall` | 搜。向量相似度和关键词匹配融合排序，旧条目随时间衰减，返回结果带 id、年龄和来源。加 `include_superseded` 能翻历史。 |
| `flymemory_auto` | 一次调用同时做召回和存储，钩子用的就是它。 |
| `flymemory_recall_index` / `flymemory_get_memory` | 先看索引再读正文：先给一行摘要，觉得有用再取全文。 |
| `flymemory_supersede` | 标明「这条过时了，被那条取代」。 |
| `flymemory_state_lookup` / `flymemory_state_history` | 某个实体键当前的值，或者它的全部历史。 |
| `flymemory_consolidate` | 把几条归纳成一条结论，原始条目留着当证据。 |
| `flymemory_find_conflicts` / `flymemory_insights` | 看起来互相矛盾的配对；有价值但快衰减掉的条目。 |
| `flymemory_forget` / `flymemory_cleanup` | 删单条，或者按衰减阈值批量清理。 |
| `flymemory_session_pack` | 近期轨迹 + 最新结论的短包。 |
| `flymemory_stats` | 条数、年龄、访问次数。 |

另外两个钩子自己跑，不用模型操心：

- **每条提示**：把提示存下来，再把匹配得上的记忆附到这一轮里。纯本地检索，不调用模型。
- **会话开始**：塞一个近期活动的恢复包，新会话不至于两眼一抹黑。

服务不在线时两个钩子都静默跳过，也绝不会卡住回合。

## 需要什么

- 带 `@deepseek-ai/dsh-mcp-client` 和 `@deepseek-ai/dsh-hooks-claude-code` 的
  DeepSeek Harness。在 `0.2.0-rc.2` 上开发验证。
- Node 20 以上（插件本体和 CLI）。
- Python 3.10 以上，装引擎的依赖：

  ```bash
  pip install torch sentence-transformers "mcp>=1.30,<2" numpy
  ```

  解释器不用你指定：插件会依次看 `FLYMEMORY_PYTHON`、常见安装位置、`PATH`，而且
  **先确认这个解释器真能 import `sentence_transformers`** 才选它。机器上装了好几个
  Python 的时候，这一步能省掉半小时的「为什么起不来」。

  第一次运行会把多语言嵌入模型（约 470MB）下载进 HuggingFace 缓存，之后离线可用。

## 安装

用 `plugin_manager` 指向本目录（绝对路径）：

```
plugin_manager(action: "install_bundle", target: "/absolute/path/to/dsh-flymemory")
```

或者打开 Web 侧边栏的 **Plugins** 页面，选这个文件夹。harness 重新加载后会有三行
插件起来（`flymemory`、`flymemory-mcp`、`flymemory-hooks`），工具就出现了。

自检：

```bash
node bin/flymemory.mjs status     # 解析出来的配置 + 当前实际状态
node bin/flymemory.mjs doctor     # 解释器、依赖、端点
```

然后让模型记一条东西再搜一次。工具没出来，`doctor` 基本能告诉你卡在哪。

卸载就是反过来：

```
plugin_manager(action: "remove_bundle", target: "dsh-flymemory")
```

记忆文件不会被删，想清掉自己动手。

## 运行方式

bundle 插入三行：

| 行 | 模块 | 干的事 |
|---|---|---|
| `flymemory` | 本包 | 探端口；没人应答就拉起引擎；生成钩子配置 |
| `flymemory-mcp` | `@deepseek-ai/dsh-mcp-client` | 把服务端的工具变成 `mcp__flymemory__<tool>` |
| `flymemory-hooks` | `@deepseek-ai/dsh-hooks-claude-code` | 在对应事件上跑那两个钩子脚本 |

harness 启动时第一行先探 `127.0.0.1:8791`：已经有 FlyMemory 服务在应答就直接复用，
两个 harness 窗口共享同一个引擎和同一份记忆；端口空着就后台把自带引擎拉起来，等它
就绪了再让下一行去连；端口被别的程序占着，插件只报一句，不去动它——不是自己起的
进程，绝不 kill。

冷启动偏慢：引擎要先 import torch 才能开始监听，大概 15–20 秒。这一行默认最多等 25 秒，
没等到的话 mcp-client 会自己重连补上。

引擎彻底起不来（没有解释器、缺依赖、端口冲突）时 harness 照样启动，只是没有这些工具，
原因写在日志里。

## 配置

环境变量，harness 启动前读取：

| 变量 | 默认值 | |
|---|---|---|
| `FLYMEMORY_MCP_PORT` | `8791` | 服务端口 |
| `FLYMEMORY_MCP_URL` | `http://127.0.0.1:<port>/mcp` | 完整端点 |
| `FLYMEMORY_DATA_DIR` | `$DSH_HOME/flymemory-data` | 记忆、日志、钩子配置、pid |
| `FLYMEMORY_PYTHON` | 自动探测 | 引擎用的解释器 |
| `FLYMEMORY_DEVICE` / `FLYMEMORY_MODEL` | `cpu` / 多语言 MiniLM | 传给引擎 |

其余参数写在你自己 profile 的 `cordis.patch.yml` 里，挂在对应行上。注意 patch 条目是
**整体替换** `config` 的，需要的字段要写全：

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    dataDir: 'D:\dsh-memory'
    libraryPath: 'D:\dsh-memory\my-lib.pkl'
    pythonExe: 'C:\Python313\python.exe'
    device: cpu
    readinessTimeoutMs: 25000   # 0 = 不等待，工具晚点自己出来
    shutdownOnDispose: true     # 退出时关掉本次拉起的引擎
    writeHooksConfig: true
    seedFromUpstream: false     # 默认不导入别的库
    upstreamLibrary: ''         # 想导入就同时配这两项
    autostart: true
```

其余字段在 [lib/index.js](lib/index.js) 的 `resolveOptions()` 里，多余的键会被忽略。

## 记忆存在哪

`$DSH_HOME/flymemory-data/flymemory_v3.pkl`，就一个文件，备份和删除都很直接。同目录还有：

- `hooks.json` — 每次激活重新生成，描述那两个钩子
- `engine.log` — 引擎进程的 stdout/stderr
- `server.log` — 引擎自己的日志
- `server.pid` — 给 `flymemory stop` 用来找进程

不想通过 harness、单独把服务跑起来：

```bash
node bin/flymemory.mjs start
node bin/flymemory.mjs stop
node bin/flymemory.mjs log -n 40
```

装成 bundle 之后，同一个 CLI 会被链接到 profile 里：
`<profile>/node_modules/.bin/dsh-flymemory`。

## 关于钩子

默认是开着的，毕竟「自动记忆」就是这个插件的意义。但它具体做了什么，值得说清楚：

- 你发的每条提示都会写进记忆文件。全在本地、开销很小，但它确实是一份输入记录。
- 召回的内容会附到当前回合，占一点上下文，实测大概每轮 1.5KB。
- 长得像密钥的内容不会入库：引擎会拒掉 `ghp_`、`github_pat_`、`sk-ant-`、`AKIA`、
  私钥头这类文本。

只想要工具、不想要自动化：

```
plugin_manager(action: "set_plugin", target: "include:flymemory-hooks", enabled: false)
```

## 导入已有的库

默认什么都不导入。如果你手上已经有一份 FlyMemory 的库想搬进来，指一次就行：

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    upstreamLibrary: 'D:\path\to\flymemory_v3.pkl'
    seedFromUpstream: true
```

下次激活时，只要目标文件还不存在就复制过去，源文件保持不动。两边格式一致，以后想搬出去
也可以。

## 想改代码

```bash
node --check lib/index.js    # 语法
npm test                     # 单元测试，不需要 Python，约 0.3 秒
node tests/engine_smoke.mjs  # 打真引擎的端到端（需要 torch）
python tests/http_smoke.py   # 对着运行中的服务做协议检查
```

`npm test` 盯的是容易出错的那几块：端口和库的默认值、配置优先级、生成的钩子文件、
端点探测。engine smoke test 在临时目录里用真的 Python 引擎跑一遍 `apply()`——需要
torch 和 sentence-transformers，没装会打印 SKIP。CI 在 Linux、macOS、Windows 上跑
Node 20 / 22 / 24，加上语法检查和单元测试。

```
lib/index.js        Host 端：探端口、管引擎、生成钩子配置
bin/flymemory.mjs   CLI，复用同一套函数
cordis.patch.yml    这个 bundle 插入的三行
python/             随包的 FlyMemory 引擎（服务端、钩子、引擎包）
locale/{en,zh}.json Plugins 页面显示的标题和描述
tests/              单元测试、引擎冒烟、协议冒烟
```

## 不太行的地方

- **第一次启动要 15–20 秒**，都是 torch 的加载时间。嫌久就把 `readinessTimeoutMs`
  设成 0，harness 立刻启动，工具几秒后自己出现。
- **Python 依赖比较重**。插件不会替你装，`doctor` 会告诉你缺什么。
- **DSH 没有 `PreCompact` 钩子**，所以恢复包挂在会话开始，而不是压缩之后。
- **工具名很长**。`mcp__flymemory__flymemory_recall` 是 mcp-client 的命名规则
  （`mcp__<server>__<tool>`）决定的，稳定，但不好看。
- **一个端口一个引擎**。两个 harness 共用；要第三份实例得显式配独立端口和独立库。
- **库越大召回越慢**。上游在 CPU、约 1400 条时测到每查询 ~11ms。

## 许可

MIT，见 [LICENSE](LICENSE)。

随包携带的 FlyMemory 引擎同样是 MIT，Copyright (c) 2026 Junrong Du。
具体复制了哪些文件、改了哪些、为什么改，都写在
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 里。
