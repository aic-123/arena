# 样本输入 0001–0020

**来源**：需求方 2026-09-25 直接提供。**输入是人写的，不是我编的。**
（此前的 19 条 AI 占位输入已由本文件取代，`placeholders.md` 已删除。）

```
╔══════════════════════════════════════════════════════════════════════════╗
║  两个字段都是【折算】的，不是需求方手划的（2026-09-25 授权）。           ║
║                                                                          ║
║  proposed_count   20 / 20 有值                                           ║
║  boundaries        4 / 20 有值（0002 0006 0008 0009，全部内容节点逐字）   ║
║                   16 / 20 空着 —— **每条都写了卡在哪个节点、差多少**，   ║
║                   不是笼统的「折不出来」。见 boundaries_basis 那行。      ║
║                                                                          ║
║  ⇒ 用这份标注算出的「切分数一致率」不是独立观测：标签是需求方的，        ║
║    折成标注的是我，两边有一条共同的来源（§C5.5.1 第 2 条）。             ║
║     引用时必须连着这句一起引。                                           ║
║  ⇒ 「边界一致率」只在 4 条上算得出，而它们全是逐字样本 ——               ║
║     真正需要参照物的 18 个「转述」节点一个都没折出来。这条依赖仍然悬着。  ║
╚══════════════════════════════════════════════════════════════════════════╝
```

**折算规则、逐条明细、工具**：`annotation-derived.md`（对齐工具 `align.py`，可重跑）。
**逐字下标（供你手划）**：`annotation-sheet.md` —— 那 16 条只有你能填。

每条的 `agent_filled` 记着这一条的来源。**没有一条是需求方手划的** ——
`proposed_count` 和 `boundaries` 全部来自折算，写在那里的是它的出身，
不是一句免责声明。

`checks.py` 的 **B8** 只保证「声明为`待需求方标注`的区块，标注区必须是空的」。
本文件的 20 条现在已经**不是**那个状态了，所以 **B8 从这里撤了** ——
它现在一条都罩不住（见 `checks.py` 里 B8 的 docstring 与 `samples/README.md`）。

---

---

## 分割器实际跑出来的结果（规则集 `zh-clause-2`）

**5 / 20 被切开，15 / 20 整块未切。** 这是实数，不是估计。

| 结果 | 条目 |
|---|---|
| 切开 | 0004 0007 0011 0012 0013 |
| 整块未切 | 0001 0002 0003 0005 0006 0008 0009 0010 0014 0015 0016 0017 0018 0019 0020 |

**15 条里要分两种情况，别混为一谈：**

**(a) 本来就该是 1 个命题** —— 不算缺陷，等你的标注确认：
0001「远程办公的效率比坐办公室高」、0002「大学文凭的回报这几年在下降」

**(b) 规则集根本不认识那些词** —— 这是缺陷，而且是我现在就能说清的：
`zh-clause-2` 只收了 `因为/由于/鉴于` 和 `所以/因此/因而/于是/导致/致使/从而/故`。
这 20 条里用到的下面这些，**一个都没收**：

| 词 | 条目 | 它其实是什么 |
|---|---|---|
| `但` / `可` | 0005 0015 0017 0018 0019 | 转折 |
| `而且` | 0006 | 并列 |
| `说明` | 0005 0009 0014 | 推断（个案 → 结论） |
| `既然…那` | 0008 | 成对关联词 |
| `取决于` | 0020 | 条件依赖，近 `§C4` 的 `assumes` |
| `真正的问题是` / `没人问` | 0018 0019 | 用户**自己重设议题**（`§C2.0`） |
| `为什么` | 0019 | 追问 —— 它提出的问题，不是命题 |

0003 / 0010 是另一种：语气词（「说真的」）和反问（「怎么可能是专家」），
切不切、怎么切，**得看你怎么标**——我不猜。

### 改过一次：0007

第一次跑出来是这样，**是 bug，不是精度不够**：

```
0007  他成绩好是因为聪明，不是因为努力。
  [cause] 聪明   [effect] 不是因为努力   ← 「不是因为努力」凭什么是「聪明」的果？
```

`_clauses_of` 的 cause 分支把连接词**前面**那截整个丢掉了，于是真正的果
（「他成绩好」）没成为命题，而逗号后的下一句被硬当成「果」。
后果不是"切得糙"，是**用户会在确认界面被问「『不是因为努力』是不是你要说的结果命题？」**
——那是机器造成的歪曲，会白白烧掉一次换说法信号（`§C5.6` ② 的归因口径就废了）。

已修（`zh-clause-1` → `zh-clause-2`，版本号按 `§C5.5` 第 5 条一起升）：

```
0007  [cause] 聪明  [effect] 他成绩好  [causal] 是因为  [clause] 不是因为努力
```

顺带：`是` 是系词，属于「是因为」这个因果结构本身，收回连接词里，不留在果命题上。

### 这 5 条切开的，也还没有一条被验证过

切开了不等于切对了。0004 / 0011 / 0012 / 0013 都是 `所以` 一个模式，
规则集认它，**不代表它切得符合你的意思**。0005 那种「个案 → 整体」的推广
（我朋友在互联网大厂 → 这个行业就是压榨）现在被 0011 切成了「因 = 我朋友…，果 = 这个行业就是压榨」,
但那个推论跳跃**没有任何一条边在表达它**。这是 `§C4` 的 `challenged_by` 本来该管的事，
现在没人管。

---

## 0001

```yaml
input: "远程办公的效率比坐办公室高。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 1
  proposed_count_basis: 标签节点数 1
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 85%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0002

```yaml
input: "大学文凭的回报这几年在下降。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 1
  proposed_count_basis: 标签节点数 1
  boundaries: [[0, 13]]
  boundaries_basis: 折算 —— 全部 1 个内容节点逐字出现在原文里，切片直接对上（samples/align.py，MIN_RUN=3）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0003

```yaml
input: "说真的，现在谁还看纸质书啊。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 1
  proposed_count_basis: 标签节点数 1
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 33%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0004

```yaml
input: "现在的工作强度太大了，所以大家都不愿意往上爬。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 3
  proposed_count_basis: 标签节点数 3
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n2(覆盖 75%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0005

```yaml
input: "他工资不低，但每个月都存不下钱，说明他不会理财。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 3
  proposed_count_basis: 标签节点数 3
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 67%)、n2(覆盖 88%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0006

```yaml
input: "AI 会替代大部分重复性工作，而且这个过程比大家想的快。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: [[0, 14], [17, 27]]
  boundaries_basis: 折算 —— 全部 2 个内容节点逐字出现在原文里，切片直接对上（samples/align.py，MIN_RUN=3）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0007

```yaml
input: "他成绩好是因为聪明，不是因为努力。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 3
  proposed_count_basis: 标签节点数 3
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 60%)、n2(覆盖 0%)、n3(覆盖 0%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0008

```yaml
input: "既然大家都在考研，那我也得考。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 3 − 1 个标着「原文未出现」的节点（Assumption 且覆盖率 0）
  boundaries: [[2, 8], [10, 14]]
  boundaries_basis: 折算 —— 全部 2 个内容节点逐字出现在原文里，切片直接对上（samples/align.py，MIN_RUN=3）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0009

```yaml
input: "这家公司开始裁员了，说明它快不行了。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 3 − 1 个标着「原文未出现」的节点（Assumption 且覆盖率 0）
  boundaries: [[0, 8], [12, 17]]
  boundaries_basis: 折算 —— 全部 2 个内容节点逐字出现在原文里，切片直接对上（samples/align.py，MIN_RUN=3）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0010

```yaml
input: "他连这个都不懂，怎么可能是专家。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 3 − 1 个标着「原文未出现」的节点（Assumption 且覆盖率 0）
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 0%)、n2(覆盖 60%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0011

```yaml
input: "我朋友在互联网大厂，每天加班到十点，所以这个行业就是压榨。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n2(覆盖 85%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0012

```yaml
input: "有研究说每天喝咖啡能降低心脏病风险，所以我每天喝三杯。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 75%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0013

```yaml
input: "德国的职业教育做得好，所以他们的制造业工人水平高。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 3
  proposed_count_basis: 标签节点数 3
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 80%)、n2(覆盖 78%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0014

```yaml
input: "我试过早起，坚持了两周就放弃了，说明早起不适合我。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n2(覆盖 55%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0015

```yaml
input: "你说读书有用，但很多成功的人根本没读多少书。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 36%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0016

```yaml
input: "这个数据是三年前的，现在情况早变了。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 36%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0017

```yaml
input: "你的结论建立在「人会理性选择」这个前提上，但现实中不是这样。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 3
  proposed_count_basis: 标签节点数 3
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n3(覆盖 40%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0018

```yaml
input: "大家在争该不该加班，但真正的问题是：加班费到底算不算劳动报酬的一部分。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 50%)、n2(覆盖 25%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0019

```yaml
input: "都在讨论要不要限制孩子玩游戏，可没人问：为什么孩子会沉迷？"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 2
  proposed_count_basis: 标签节点数 2
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n1(覆盖 78%)、n2(覆盖 0%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```

## 0020

```yaml
input: "这个政策好不好，取决于你说的是短期还是长期 —— 短期有效，长期有害。"
input_origin: 需求方 2026-09-25
annotation_status: 需求方授权折算
annotation:
  proposed_count: 4
  proposed_count_basis: 标签节点数 4
  boundaries: []
  boundaries_basis: 折不出来 —— 卡在 n2(覆盖 0%)：节点文本是转述不是原文切片，边界得推，推出来的是我的不是原文的（annotation-derived.md §2）
  agent_filled: 需求方授权折算（2026-09-25）—— 依据 samples/Arena-MVP-测试样本-标签.md（2026-09-26 起这份文件在仓库内），非需求方手划
```
