# dsh-flymemory

[English](README.md) | 中文

**给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 用的私有长期记忆插件。**
把 [FlyMemory](https://github.com/aujurd22/flymemory) 接进 DSH：15 个记忆工具 +
自动召回/自动捕获钩子 + 受管本机引擎，**独占端口、独占库文件**，不会和另一个
FlyMemory 实例（例如 8765 上那个）互相污染。

```
DSH 会话 ──┬── mcp__flymemory__flymemory_*   15 个工具，由模型判断该记什么
           └── 钩子                            自动召回 + 自动捕获
                     │
                     ▼
           FlyMemory MCP 服务  127.0.0.1:8791
                     │
                     ▼
           $DSH_HOME/flymemory-data/flymemory_v3.pkl
```

## 为什么做成插件

FlyMemory 本身已经是一个标准 MCP 服务，但手工接进宿主意味着：自己起服务、挑端口、
注册钩子、保证它别死。这个插件把这一层做掉了：

- **存储独立**：默认端口 `8791`、库文件 `$DSH_HOME/flymemory-data/flymemory_v3.pkl`。
  你另一套 FlyMemory（通常 8765）既不会被连上也不会被写入，除非你显式要求导入。
- **零配置**：装完 bundle，引擎跟着宿主启动，工具以 `mcp__flymemory__*` 出现。
- **自动记忆**：钩子把每条提示转向记忆库，并把相关记忆注入本轮对话。
- **无依赖**：不 import 任何 `@deepseek-ai/*`，package.json 里没有任何 npm 依赖；
  它用到的两行插件都是 DSH 自带的。

## 环境要求

| | |
|---|---|
| DeepSeek Harness | 需要带 `@deepseek-ai/dsh-mcp-client` 和 `@deepseek-ai/dsh-hooks-claude-code` 的版本（在 `0.2.0-rc.2` 上开发验证）。 |
| Node | 20+（插件本体与 CLI）。 |
| Python | 3.10+，装 `torch`、`sentence-transformers`、`mcp>=1.30,<2`、`numpy`。 |
| 磁盘/网络 | 多语言嵌入模型（`paraphrase-multilingual-MiniLM-L12-v2`，约 470MB）首次下载进 HF 缓存，之后全程离线。 |

```bash
pip install torch sentence-transformers "mcp>=1.30,<2" numpy
```

解释器自动探测（`pythonExe`/`FLYMEMORY_PYTHON` → 常见安装位置 → `PATH`），并且会
先验证该解释器能 import `sentence_transformers` 才选中它。`dsh-flymemory doctor`
会告诉你它选了哪个、缺什么。

## 安装

在源码目录外执行，target 指向本包目录的绝对路径：

```
plugin_manager(action: "install_bundle", target: "/absolute/path/to/dsh-flymemory")
```

也可以在 Web 侧边栏 **Plugins** 页面选这个目录。安装会把它写进 profile 并选中
bundle，随后三行插件变为 active：`flymemory`、`flymemory-mcp`、`flymemory-hooks`。

验证：

```bash
dsh-flymemory status      # 端点、库文件、解释器、bundle 选中状态
dsh-flymemory doctor      # 解释器与依赖自检
```

然后让模型存一条（`mcp__flymemory__flymemory_remember`）再召回
（`mcp__flymemory__flymemory_recall`）即可。

卸载：

```
plugin_manager(action: "remove_bundle", target: "dsh-flymemory")
```

## 工具清单

调用名 = `mcp__flymemory__` + 下表名字。

| 工具 | 用途 |
|---|---|
| `flymemory_remember` | 存记忆；语义去重自动合并，`compartment` 分区，`state_key/state_value` 记实体状态并机械取代旧值 |
| `flymemory_recall` | 混合召回（稠密向量 + IDF 词法，RRF 融合，衰减加权，带 id/年龄/来源标注）；`include_superseded` 查历史 |
| `flymemory_auto` | 一次调用完成「召回 + 新颖度门控存储」，钩子走的就是它 |
| `flymemory_recall_index` / `flymemory_get_memory` | 渐进式披露：先一行摘要，再取全文 |
| `flymemory_supersede` | 标记旧记忆已被取代（判断由调用方模型做） |
| `flymemory_state_lookup` / `flymemory_state_history` | 实体键的当前值 / 全部历史 |
| `flymemory_consolidate` | 多条归纳为一条高阶结论，原始条目保留为证据 |
| `flymemory_find_conflicts` / `flymemory_insights` | 矛盾候选；高价值条目即将衰减提醒 |
| `flymemory_forget` / `flymemory_cleanup` | 定点删除 / 按衰减阈值定向遗忘 |
| `flymemory_session_pack` | 近期轨迹 + 最新结论（压缩恢复包） |
| `flymemory_stats` | 条数、最旧/最新、访问次数、平均衰减 |

## 工作原理

bundle patch 插入三行：

| 行 | 模块 | 作用 |
|---|---|---|
| `flymemory` | 本包 | 探测端口：有人且确实是 FlyMemory 就复用，否则拉起自带引擎；生成钩子配置 |
| `flymemory-mcp` | `@deepseek-ai/dsh-mcp-client` | 把服务端工具注册为 `mcp__flymemory__<tool>` |
| `flymemory-hooks` | `@deepseek-ai/dsh-hooks-claude-code` | 跑 `hook_auto.py`（每条提示）与 `hook_compact.py`（每次会话开始） |

启动与降级：

1. `flymemory` 探测 `host:port`。若那里回 `tools/list` 且带 `flymemory_*` 工具，
   直接复用——同一台机器上多个宿主进程共享一个引擎、一份库。端口被别的程序占用
   则只报告，绝不覆盖。
2. 否则以 `--http` 分离进程拉起自带引擎，stdio 落到 `<dataDir>/engine.log`，
   并等待（默认 25s）端口可用，让下一行首次连接就成功。
3. 引擎起不来（没解释器 / 缺依赖 / 端口被占）时宿主照常启动：工具不出现，原因在
   日志里，mcp-client 行会按退避继续重连。

## 配置

环境变量（宿主启动前读取）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `FLYMEMORY_MCP_PORT` | `8791` | 服务端口 |
| `FLYMEMORY_MCP_URL` | `http://127.0.0.1:<port>/mcp` | 完整端点 |
| `FLYMEMORY_DATA_DIR` | `$DSH_HOME/flymemory-data` | 库、日志、钩子配置、pid |
| `FLYMEMORY_PYTHON` | 自动探测 | 引擎解释器 |
| `FLYMEMORY_DEVICE` / `FLYMEMORY_MODEL` | `cpu` / 多语言 MiniLM | 透传给引擎 |

行级 `config`（优先级更高）写在 profile 的 `cordis.patch.yml`；覆盖会**整体替换**
`config`，所以要写全你需要的字段：

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    dataDir: 'D:\dsh-memory'
    libraryPath: 'D:\dsh-memory\my-lib.pkl'
    pythonExe: 'C:\Python313\python.exe'
    readinessTimeoutMs: 25000   # 0 = 不阻塞宿主启动，工具稍后由重连补上
    seedFromUpstream: false     # 默认不导入任何其他安装的库
    upstreamLibrary: ''
```

完整字段与含义见 `lib/index.js` 的 `resolveOptions()` 和英文 README 的表格。

## 隐私：钩子会写入，请知情

`flymemory-hooks` 行**默认开启**，因为「自动记忆」正是这个插件的价值：

- `UserPromptSubmit`：把每条用户提示存进本插件自己的库（`source=hook`，未经模型
  判断），并把召回结果注入本轮（约 1.5KB/轮）。
- `SessionStart`：注入近期轨迹 + 最新结论的恢复包。

只想要工具、不要自动捕获：

```
plugin_manager(action: "set_plugin", target: "include:flymemory-hooks", enabled: false)
```

凭据形态的内容在入库前就被拒绝（沿用上游过滤：`ghp_`、`github_pat_`、`sk-ant-`、
`sk-proj-`、`AKIA`、`xoxb`、私钥头等）；钩子任何异常都静默退出 0，绝不阻塞会话。
数据就是本机文件：`<dataDir>/flymemory_v3.pkl` 加日志，不往任何地方发送。

## 命令行

在源码目录里跑 `node bin/flymemory.mjs <命令>`；装成 bundle 之后同一个 CLI 会被
链接进 profile（`<profile>/node_modules/.bin/dsh-flymemory`），所以
`npx dsh-flymemory <命令>` 或直接用它也可以。

```bash
dsh-flymemory status     # 解析后的配置、实时端点、工具数、bundle 选中状态
dsh-flymemory start      # 立刻启动服务（分离进程）
dsh-flymemory stop       # 按 server.pid 停止
dsh-flymemory log -n 40  # tail engine.log 与 server.log
dsh-flymemory doctor     # 解释器、依赖、端点、路径、安装提示
```

每个命令都接受 `--port`、`--host`、`--data-dir`、`--library`、`--python`。

## 导入已有的 FlyMemory 库

插件默认不碰别的安装。真要迁移就显式来一次：

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    upstreamLibrary: 'D:\path\to\flymemory_v3.pkl'
    seedFromUpstream: true
```

下次激活时，如果 `libraryPath` 还不存在，就把该文件**复制**过来；源文件保持不动。
库格式与上游一致，两个方向都能搬。

## 开发

```bash
node --check lib/index.js        # 语法
npm test                         # 单元测试（node tests/run.mjs）：配置、钩子、端点探测
node tests/engine_smoke.mjs      # 引擎全链路（需要 torch + sentence-transformers）
python tests/http_smoke.py       # 协议级冒烟（对着运行中的端点）
```

`engine_smoke.mjs` 在临时目录里跑真实的 `apply()`，绝不碰你的记忆库；找不到合适
解释器时打印 SKIP 并以 0 退出。CI 在 Linux/macOS/Windows 上跑语法检查与单元测试。

## 已知限制

- **冷启动慢**：引擎要先 import torch（约 15–20s）才开端口，首次激活会等它；已有
  服务在跑则零延迟。想不阻塞就设 `readinessTimeoutMs: 0`。
- **Python 依赖重**：需要 torch 与 sentence-transformers，插件不会替你安装；
  `doctor` 会告诉你缺什么。
- **DSH 没有 `PreCompact`/`PostCompact` 钩子**，所以恢复包挂在 `SessionStart` 上。
- **工具名较长**：`mcp__flymemory__flymemory_recall` 是 mcp-client 的命名契约。
- **一个端口一个引擎**：多个宿主共享；要独立就配独立端口 + 独立库。
- 召回耗时随库增大（上游基准：约 1400 条时 ~11ms/查询）。
- 在 DSH `0.2.0-rc.2` 上开发验证；插件不 import 宿主代码，只依赖两行的 config
  schema，宿主升级一般不需要改本插件。

## 许可

MIT，见 [LICENSE](LICENSE)。

随包携带的 FlyMemory 引擎同样是 MIT（Copyright (c) 2026 Junrong Du）；具体文件
清单与所做改动见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
