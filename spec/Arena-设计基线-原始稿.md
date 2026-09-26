# Arena × Scaffold · 设计基线（原始稿）

> **来源**：用户 2026-09-25 提供的完整原文（此前只存在于对话中，未落盘）。
> **用途**：作为比对基准。**本文件不得改动** —— 它是原始基线。
> **原始前言**：「明天你直接把下面这份给 Agent，当成当前设计基线。我把已经达成共识的东西和"明天允许现场调整"的东西分开，避免 Agent 把我们讨论过的探索方案全当成硬需求。」

---

## 0. 一句话定位

Arena 是一个建立在 Scaffold 之上的多人结构化讨论系统。

Scaffold 负责承载对象、关系、证据、状态、版本和演化历史；Arena 负责让人围绕这些对象进行讨论、质询、分歧展开和投票。

最重要的边界：

**AI 是秘书，不是裁判。**

AI 可以：

- 分割语义
- 整理结构
- 发现相似/冲突
- 提示可能的子问题
- 辅助用户补充结构

AI 不可以：

- 判断哪一方正确
- 给论点打真值分数
- 自动决定观点权重
- 偷偷修改用户表达
- 替用户确认语义
- 根据自己的判断生成新的事实进入知识结构

## 1. 总体结构

```
┌─────────────────────┐
│       Arena         │
│  多人讨论 / 投票 / 交互  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      Scaffold       │
│                     │
│      Object         │
│      Relation       │
│      Evidence       │
│      Provenance     │
│      State          │
│      Revision / History │
└──────────┬──────────┘
           │
   ┌───────┼───────┐
   ▼       ▼       ▼
 Topic  Argument  Evidence
   │       │       │
   └───────┴───────┘
           ▼
      Debate / Arena
```

核心原则：

Arena 不重新发明一套知识结构，而是把 Arena 中产生的东西作为 Scaffold Artifact 管理。

## 2. Scaffold 最小能力

第一版不要把 Scaffold 做成宇宙级框架。

至少需要：

```
Artifact  Relation  Evidence  Challenge  Revision
```

**Artifact**

任何具有独立身份、状态、生命周期的对象。

例如：

```
Topic  Claim  Evidence  Argument  Debate  Vote  Subtopic
```

**Relation**

第一版可以先支持：

```
contains  derived_from  supports  contradicts  refines  related_to
```

注意：

Relation 本身也是可追踪对象，而不是数据库里随手塞一个 foreign key 就完事。

## 3. Debate 的基本结构

一个讨论不应该只是：

```
用户 A：一大段文字
用户 B：一大段文字
```

而应该逐步结构化为：

```
Topic
│
├── Position / Claim
│   │
│   ├── Evidence
│   ├── Mechanism
│   ├── Assumption
│   └── Counterexample
│
├── Opposing Claim
│   │
│   ├── Evidence
│   ├── Mechanism
│   └── Counterargument
│
└── Vote
```

逻辑关系：

```
Evidence  →  supports / contradicts / qualifies  →  Claim
Claim     →  assumes                             →  Assumption
Claim     →  explains                            →  Mechanism
Claim     →  challenged_by                       →  Counterargument
```

不要把这些关系简化成"证据分数"。

## 4. 语义过滤 / 用户确认

这是 Arena 的入口防线。

用户输入自然语言：

> 「现在社会对女性太好了，所以她们根本不懂男性压力。」

AI 不直接把它当成一个完整 Claim。

先拆：

```
A. 女性获得了更好的社会待遇
B. 女性不足够理解男性压力
C. A 导致 B
```

然后：

```
AI 分割
   ↓
用户确认
   ↓
进入 Scaffold
```

关键：

语义过滤解决的是"这句话到底包含几个命题"，不是"这句话是真是假"。

AI 可以提出结构。

最终确认权属于用户。

## 5. 证据系统

第一版不要设计一个：

```
evidence_score = 8.7
```

这种东西。

因为一旦这样做，系统马上开始偷偷成为裁判。

证据应该记录属性：

```
Evidence
├── source / provenance
├── accessibility
├── directness
├── reproducibility
├── methodology
├── sample / selection
├── representativeness
├── temporal validity
├── independence
└── conflict / incentive
```

同时记录它和 Claim 的关系：

```
Evidence X
│
├── supports    → Claim A
├── qualifies   → Claim B
└── contradicts → Claim C
```

最重要的是：

证据的作用是相对于具体 Claim 定义的。

同一个 Evidence：

```
在 Q1 中 → 很相关
在 Q2 中 → 可能只是边缘信息
```

所以权重不是 Evidence 自带的属性。

## 6. 上层 Scaffold 动态定义"权重"

这是目前比较重要的设计突破。

底层 Scaffold：

```
记录事实：
  谁是什么
  谁支持谁
  谁反驳谁
  来源是什么
  什么时候发生
  经历过什么修改
```

上层 Scaffold：

```
在这个具体上下文中：
  什么重要
  什么相关
  什么值得展开
  什么应该被关注
```

因此：

**Weight 是关系/上下文属性，不是对象固有属性。**

可以理解成：

```
Bottom Scaffold
│
│ objects / relations / provenance
▼
Context Scaffold
│
│ contextual interpretation / relevance
▼
Arena Presentation
```

这样避免把：

```
"系统认为这个证据重要"
```

硬编码成：

```
"这个证据本身就是重要的。"
```

## 7. Nested Scaffold

这是目前最值得作为 MVP 展示的结构能力。

一个 Debate 本身可以成为另一个 Scaffold 中的 Artifact。

例如：

```
Scaffold S0
│
└── Debate Q0
    │
    ├── Claim A
    ├── Claim B
    └── Evidence X
        │
        └── 发现一个独立分歧
            │
            ▼
Scaffold S1
│
└── Debate Q1
```

然后：

```
Q1
│
└── derived_from → Q0
```

甚至：

```
Q0
├── related_to   → Q2
├── refines      → Q3
└── derived_from → Q4
```

核心思想：

结构本身也可以成为内容。

一个 Debate 可以成为另一个 Debate 的材料。
一个 Argument 可以成为另一个 Topic 的讨论对象。
一个 Topic 可以成为更高层结构的一部分。

## 8. 但禁止无限嵌套

这个必须写进设计原则。

Nested 不是默认行为。

默认：

```
flat + relation
```

只有满足：

```
① 独立生命周期
② 独立上下文
③ 脱离父节点后仍具有独立意义
④ 需要独立讨论 / 修正 / 验证
```

才考虑：

```
nest
```

否则只是：

```
related_to
```

或者：

```
derived_from
```

尤其禁止：

"因为 Scaffold 可以解释 Scaffold，所以再造一个 Scaffold 解释它。"

无限递归最终会重新制造新的权威层。

所以：

能力越通用，越需要限制它的适用边界。

## 9. 多观点，不强行二元化

Arena 初始可以简单做：

```
A ↔ B
```

但数据结构不要写死二元。

因为讨论过程中可能出现：

```
A
├── A1
├── A2
└── A3
B
```

甚至：

```
A  B  C  D
```

关键规则：

不要把旧票自动分裂

例如：

```
原始：
A = 61%
B = 39%
```

后来：

```
A → A1 + A2
```

不能自动推导：

```
A1 = 30%
A2 = 31%
```

旧票属于旧问题版本。

新结构应该产生新的投票上下文。

## 10. Parent / Child 与 Vote

必须区分：

**结构继承 ≠ 判断继承**

可以：

```
A1 derived_from A
```

但不能：

```
A1 inherits votes from A
```

因此：

```
Q0
└── A
    ├── A1
    └── A2
```

Q0 的投票历史保留。

A1/A2 如果成为独立问题：

```
Q1
├── A1
├── A2
└── B
```

重新投票。

这样历史不会被篡改。

## 11. 分裂机制

证据、用户行为、论证结构可以触发：

"这里可能存在一个独立问题。"

但：

```
Evidence
   ↓ suggest possible subtopic
用户确认
   ↓
创建新 Topic / Scaffold
```

而不是：

```
Evidence
   ↓
系统自动生成子节点
```

原则：

群众拥有提出和展开议题的权力；系统负责发现相似性、差异性和结构性分歧。

任何结构关系都应该：

```
可确认  可拒绝  可修改  可追踪
```

## 12. 投票

投票只是：

公共偏好 / 公共判断的记录。

不是：

真理判定。

所以界面最好明确区分：

```
Public Preference
Argument Support
Evidence Status
```

例如：

```
公众倾向： A 61%  B 39%
论点支持： A：4 / 6 claims 有支持材料
证据状态： 3 verified  3 unresolved
主要争议： Premise P1
```

不要混成：

```
A 61% → A 更正确
```

这是系统边界。

## 13. 匿名投票

初始设计：

匿名

随机抽样 / 随机展示议题

尽可能降低身份、粉丝、声望影响

同时记录：

```
sampling
question_version
argument_version
timestamp
prior_results_visible
repeat_participation
```

因为：

投票结果本身会影响后续投票。

如果用户先看到：

```
A 80%
B 20%
```

再投票，那么这个结果已经成为实验变量。

所以：

**Vote Result + Observation Condition**

应该一起记录。

## 14. 并发设计——明天重点观察

Arena 正好可以自然产生真实的多用户并发场景。

至少关注：

```
User A ──┐
         ├──> same Topic / same Scaffold state
User B ──┘
```

可能发生：

**并发提交**

```
A confirm Claim
B confirm Claim
```

**并发修改**

```
A edit v1 → v2a
B edit v1 → v2b
```

**并发创建子议题**

```
A creates "男性压力"
B creates "男性压力问题"
```

**投票竞争**

```
Vote + Topic close
```

**AI / Human race**

```
AI 正在整理 v1
   ↓
用户修改 v1 → v2
   ↓
AI 结果回来
```

这时候就会真正逼出：

```
versioning
optimistic locking
idempotency
conflict handling
event/history
stale client handling
transaction boundary
cache consistency
realtime update
```

不要现在为了简历预先上 Kafka、Redis Cluster、分布式锁。

先让真实并发把问题打出来。

## 15. 版本模型

因为你的核心原则是：

记录，不停止，不限制。

所以不要默认覆盖：

```
Q → update → Q
```

更适合：

```
Q(v1)
│
├── user A → Q(v2a)
│
└── user B → Q(v2b)
```

之后：

```
merge  compare  reject  derive
```

都应该留下历史。

这会自然形成：

**Current State + Revision History**

而不是单纯 CRUD。

## 16. MVP 的真正目标

不是做一个完整 Reddit。

也不是做一个完整"AI 辩论平台"。

MVP 只需要证明这一条链：

```
用户提出问题
   ↓
AI 语义分割
   ↓
用户确认
   ↓
形成 Topic / Claim
   ↓
双方 / 多方提交观点
   ↓
Evidence / Mechanism / Assumption
   ↓
Challenge
   ↓
发现分歧
   ↓
产生可能独立的 Subtopic
   ↓
用户确认
   ↓
新 Scaffold / Debate
   ↓
独立讨论
   ↓
Vote
   ↓
上下层关系可追踪
```

如果这条链跑通，Nested Scaffold 就不再只是一个架构想法，而是一个真正存在的系统能力。

## 17. 明天允许现场调整的部分

这些现在不要当圣经：

```
具体数据库 schema
具体前后端框架
Redis 是否需要
消息队列是否需要
WebSocket / SSE
锁策略
具体 API
具体 UI
具体投票算法
自动分裂阈值
自动合并算法
```

原则：

先由样本和实际交互产生约束，再决定实现。

## 18. 明天绝对不要被 Agent 偷改的东西

这几个属于设计红线：

- ❌ AI 自动判定谁正确
- ❌ AI 自动修改用户原意
- ❌ AI 生成内容直接成为 verified knowledge
- ❌ Vote = Truth
- ❌ Popularity = Evidence
- ❌ Parent vote 自动继承给 Child
- ❌ Evidence 自带固定 truth score
- ❌ Nested = 默认行为
- ❌ 自动产生无法追溯的结构
- ❌ 覆盖历史状态而没有 revision

以及最重要的一条：

如果 Agent 为了实现方便，把一个本来应该由 Scaffold 表达的关系硬编码进 Arena，停下来检查。

因为我们现在最怕的不是代码丑。

而是底层抽象悄悄被应用层污染。

## 19. 最终分层图

明天你甚至可以直接给 Agent 看这个：

```
┌─────────────────────────────────────────────┐
│                  Arena                      │
│                                             │
│  Topic / Debate / User / Vote / Interaction │
│                                             │
│  语义确认 · 论证 · 质询 · 投票 · 分歧展开      │
└──────────────────────┬──────────────────────┘
                       │
                       │ uses
                       ▼
┌─────────────────────────────────────────────┐
│                 Scaffold                    │
│                                             │
│  Artifact                                   │
│  Relation                                   │
│  Evidence                                   │
│  Provenance                                 │
│  State                                      │
│  Revision / History                         │
│                                             │
│  supports / contradicts / refines /         │
│  derived_from / contains / related_to       │
└──────────────────────┬──────────────────────┘
                       │
                       │ can compose
                       ▼
┌─────────────────────────────────────────────┐
│              Nested Scaffold                │
│                                             │
│  Topic → Debate → Subtopic → Debate ...     │
│                                             │
│  但：默认 flat + relation                    │
│      独立生命周期才允许 nest                  │
└─────────────────────────────────────────────┘

AI：
┌─────────────────────────────────────────────┐
│      Secretary / Organizer / Detector       │
│                                             │
│  分割 · 整理 · 找相似 · 找冲突 · 提醒分歧     │
│                                             │
│              ≠ Judge                        │
└─────────────────────────────────────────────┘
```

---

## 明天的工作顺序

我建议 Agent 直接按这个顺序，不要一上来造完整系统：

```
01 先读样本
   ↓
02 找最小 Artifact
   ↓
03 建最小 Scaffold
   ↓
04 跑通一个 Debate
   ↓
05 加语义确认
   ↓
06 加 Claim / Evidence / Challenge
   ↓
07 加 Revision
   ↓
08 加 Vote
   ↓
09 故意制造两个用户同时操作
   ↓
10 记录第一个真实并发问题
   ↓
11 再偷成熟方案
   ↓
12 修掉它
   ↓
13 再跑
```

最重要的是第 09 步。

明天不是为了证明"我们已经设计完了"。

而是为了让系统第一次真正开始反过来教育我们。

你负责一天把它拽起来；Agent 负责帮你把手弄脏；我负责在旁边盯着，看到哪里开始偷换概念就喊停。
