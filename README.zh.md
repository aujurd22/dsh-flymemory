# dsh-flymemory

[English](README.md) | 中文

[![npm](https://img.shields.io/npm/v/dsh-flymemory)](https://www.npmjs.com/package/dsh-flymemory)
[![Listed on dsh-plugin.org](https://dsh-plugin.org/badges/listed.svg)](https://dsh-plugin.org/plugins/aujurd22/dsh-flymemory)

给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 用的长期记忆插件——
**它还记得"什么已经变了"。**

你大概熟悉这个场景：新开一个会话，又得从头讲一遍——这项目是干什么的、我们定过什么、试过什么、
哪儿炸过。讲完十分钟，它信心满满给你一套你三周前就放弃的方案，因为那套方案还在记忆里，
而记忆里没人记下"它已经不作数了"。

这个插件主要解决的就是后半句。市面上的记忆工具大多在解决"记得更多"，
它顺手解决另一个问题：**别再拿过期的当真。**

## 试一下

三种装法，装的是同一个插件：

```bash
npm i dsh-flymemory          # 命令行，永远是最新版
```

- **在 DSH 里**：侧栏 → 插件 → 市场 → 搜 `flymemory` → 安装。
  （市场索引每天同步一次，今天的版本可能明天才显示；想立刻拿到最新版就用
  `npm i dsh-flymemory@latest`。）
- **网页**：[dsh-plugin.org](https://dsh-plugin.org/plugins/aujurd22/dsh-flymemory) 和
  [awesome-dsh-plugin.com](https://awesome-dsh-plugin.com/p/aujurd22/dsh-flymemory/) 都有条目。

装完先看状态：

```bash
dsh-flymemory status         # 在不在跑、库在哪、里面有多少条
```

然后在 DSH 里问一个"你昨天那个对话里说过的事"。如果它能接上你没交代的上下文，就是通了。

装的时候有两件事会慢一次：

- **第一次启动约 10–13 秒**（要把嵌入模型读进内存），之后服务常驻，回答是毫秒级的；
- **首次安装会下载一个 915 MB 的多语言嵌入模型**，只下一次。

所有东西都在你本机：一个库文件、一份使用账本，都在 `$DSH_HOME/flymemory-data`。
不往任何地方发。

## 它到底干了什么

| | 你得到的 |
|---|---|
| **跨会话、跨客户端记得住** | ZCode / Qoder / DSH 可以共用一份库，每条都标着是哪个客户端写的，召回时能看出"是谁告诉谁的" |
| **知道什么过期了** | 结论变了，旧的那条不是删掉而是**退役**：留着 `valid_from`/`valid_to` 和取代它的那条，但不再出现在正常召回里 |
| **能回答"当初我为什么这么以为"** | 退役的条目连日期一起留着，历史可以翻，而不是被覆盖 |
| **每轮开销很小** | 每轮只注入一段有上限的指针索引（≤1.2 KB），模型需要哪条再去拉全文 |
| **能看见"用了没有"** | `usegate.jsonl` 记录每个被注入的指针：展开了、又召回了、还是被取代了——记忆到底有没有用，第一次能查 |
| **会自己收拾** | 每日一趟：用模型合并重复、退役过期状态、整组折叠、把文件缩小 |

## 最常用的三条

```bash
dsh-flymemory doctor     # 为什么跑不起来（python / 模型 / 端口 / 库）一屏看完
dsh-flymemory ledger     # 上一轮注入了什么，哪些真的被用了
dsh-flymemory wiki       # 把库导成一份能读的「当前真相」页
```

想让它自己想想：

```bash
dsh-flymemory dream --window 90          # 把最近 90 分钟蒸馏成能长期用的结论
dsh-flymemory dream --reorganize         # 每日宏观整理（见下）
dsh-flymemory organize --apply           # 退役过期状态、合并完全相同文本
dsh-flymemory vacuum --older-than 30     # 真把文件变小
```

## 为什么不直接用「笔记表 + 余弦检索」

笔记表负责存和找相似的，这只干了一半。另一半是：**这么多条里，哪条还是真的。**

| | 笔记表 + 余弦 | dsh-flymemory |
|---|---|---|
| 一个事实变了 | 新旧一起被召回 | 旧条目退役，正常召回看不见 |
| 同一句话说了两遍 | 越堆越多 | 分级去重：合并或加强 |
| "X 现在是什么" | 你自己拼查询 | `state_key` 保留当前值 + 完整历史值 |
| 新旧 | 都一样新 | 幂律衰减按保留度加权 |
| 哪来的 | 丢了 | 每条带 `source` 和 `app:<名字>` |
| 有没有用 | 不知道 | 使用账本 |

## 每天醒一次

只按时间窗口整理，看不出"两个话题其实是一件事"；整库重写能看出来，但容易漂。每日那一趟卡在中间：
读一份有上限的摘要（8 个主题桶 × 12 条），让模型给最多三条长期结论 + 重复对，然后写成**一条**
playbook；昨天的 playbook 会被退役进这条，所以宏观视图永远只有一份，而且不会跟证据打架。

Windows 上是每天 04:30 的计划任务；其它系统一行 cron：

```
30 4 * * *  dsh-flymemory dream --reorganize --reorganize-apply
```

## 效果到底怎么样

一句话：公开的长记忆基准（LongMemEval oracle，120 题，同一个 reader、同一个 judge）上，
这个插件的协议是 **69.2%**，朴素检索 50.8%，而 token 只有"把整段历史塞进 prompt"的三分之一。
和市面上常用的那个记忆库比是**平手**（58.3% vs 56.7%）。

它**不是**什么：不比模型更聪明，历史很短时不如直接全塞进去，也不擅长猜你的偏好。
完整数字、协议和我们自己**证伪掉的**结论都在 [docs/COMPARISON.md](docs/COMPARISON.md)。

## 诚实的局限

- **同一时间只能有一个写者。** 服务串行化写入；两个进程指向同一个库会打架，
  现在第二份会写成 `*.conflict-<时间>`，而不是悄悄覆盖。
- **偏好类很弱。** 基准数字就是这么说的，别指望画像功能。
- **比纯 JS 插件重。** Python + 915 MB 模型，对面是 SQLite 文件。想要零依赖，还有更简单的选择。
- **召回是 top-k。** 语义离得远的条目就是回不来——这是检索的本质，不是能许愿解决的问题。

## 环境要求

- DeepSeek Harness（这是个 bundle 插件）
- Python 3.10+，装 `torch` 和 `sentence-transformers`（CLI 会自己找，找不到时 `doctor` 会直说）
- Node 20+

## 配置

写在 profile 的 `cordis.patch.yml` 里，不写就用默认值：

| 键 | 默认 | 作用 |
|---|---|---|
| `port` | `8791` | 记忆服务监听的端口 |
| `libraryPath` | 数据目录下 | 用哪个库（多个客户端指同一个） |
| `workingContextChars` | `1200` | 每轮注入的指针索引大小 |
| `workingContextRefreshMs` | `45000` | 指针索引多久刷新一次 |
| `recallProtocol` | `true` | 「需要什么自己拉」的提示段 |
| `answerProtocol` | `true` | 「先抄事实再算」的提示段 |
| `checkpointContract` | `true` | 把压缩摘要折回记忆 |

## 模型能调的工具

共 23 个 MCP 工具，最常用的：

`flymemory_recall_block`（按预算读）、`flymemory_recall`（排序检索）、`flymemory_remember`、
`flymemory_supersede`（让一条事实退役）、`flymemory_state_lookup`、`flymemory_wiki`（当前真相页）、
`flymemory_organize`（日常整理）、`flymemory_vacuum`（瘦身）、`flymemory_redact`（擦掉密钥、保留审计）、
`flymemory_digest`、`flymemory_clusters`。

## 数据放在哪

```
$DSH_HOME/flymemory-data/
  flymemory_v3.pkl     库（单文件）
  usegate.jsonl        注入 → 使用 账本
  dream.json           给 `dream` 用的免费端点 key（只在本地）
```

## 许可

MIT。引擎在 [aujurd22/flymemory](https://github.com/aujurd22/flymemory)，这个包把它 vendored 到
`python/flymemory/` 并接进 harness。
