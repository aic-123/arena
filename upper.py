"""`§C7.1` 上层结构 —— **只派生，不倒流**。

上层（Context Scaffold）不设权重、不打分。它做的是同一件事：
**把已经在底层发生过很多次的形状，指出来，排到前面去。**

    ┌ 底层：这条假设被质询了 7 次，横跨 3 个立场
    │ 上层：  ↑ 它会被**排在默认展开位置**。
    │         ├ 不许写成「这个假设可疑」—— 那是真值判断（§C7.1 ④）
    │         └ 不许写进底层任何字段
    └ 上层**不是**：替用户总结「大家在争论的其实是 X」—— 那要用户自己说。

--- 信号：这一节是本模块最要紧的地方 ------------------------------------------

`§C7.1` ① 把**驱动信号**限死在一个白名单里：

| 信号 | 判定 |
|---|---|
| 投票数 / 热度 / 参与量 / 曝光量 | **禁止** —— 违反不变量 #5（Popularity ≠ Evidence） |
| 某类争议反复出现的次数 | 允许 |
| 某个假设被反复质询的次数 | 允许 |
| 某条关系被反复修正的次数 | 允许 |

**允许的只有结构性的量。** 这一条比「用哪个算法」根本得多：

    换算法（k-means → 谱聚类 → 别的）：信号还是「文本长得像」，一样违规。
    换信号（票数 → 被质询次数）：算法一行不用改，合规了。

所以本模块里**没有任何一处读票数、读热度、读参与量**。
`COUNT_SIGNALS` 是那张白名单，`checks.py` 的 B14 盯着它 —— 见该条注释。

⚠️ **为什么聚类不该出现在这一层。** 本模块的原型里考虑过聚类
（把相似命题并成一簇，用簇心当上层节点）。**否掉了**，两条理由：

1. **它的驱动信号是「文本相似度」** —— 那不是 `§C7.1` 白名单里的结构量。
   相似度是一种距离度量，不是「反复出现」。
2. **它产出的簇心是一个新断言。** 「这一簇叫 X」这句话，
   系统自己说不出来（说了就是替用户立论，`§C2.0`）。
   `§C7.1` ③ 的例外条款正指着这件事：
   「若上层归纳提议了一个新结构……那就新增了断言，必须走确认流程」。

   本模块走的路比确认更稳：**节点可以自动建，但节点不带系统给的名字。**

--- 命名：自动建、但**空着名字** ------------------------------------------------

需求方 2026-09-27 定的这一档比 `§C7.1` 原来的两档更细：

    纯重排（不新增断言）           → 不需确认
    提议新结构 + **系统给它命名**  → 必须确认（§C7.1 ③ 例外）
    提议新结构 + **名字由人给**     → ← 本模块走这条

为什么第三条能免掉确认：**命名是那句断言的**全部**内容。**
节点名留空，系统就**一句话都没说** —— 它只是把「这几条该并在一起」
这个结构判断摆出来，用**已有节点的原话**作依据。
所以 `PENDING_NAME` 不是一个占位符，它是**这条设计能成立的前提**。

--- 单向性：本模块唯一被允许写的那个字段 ----------------------------------------

`§C7.1` ④：

    底层  →  上层     派生，允许
    上层  →  底层     禁止写成事实

「上层的结果不得写入底层任何字段」——所以本模块**不改任何底层 Artifact 的内容**。
它唯一建的是一批 `Context` 节点，挂在一个显式的 `Relevance` 边上，
**这条边不是底层的真值**：把它整批删掉，底层一字不少。

危险不是「上层没被批准」，危险是**上层的相关性判断被固化成底层的真值判断**。
所以 `promote()` 里有那条硬检查：**目标节点必须已经 `active`** ——
上层归纳不许把任何东西从「未确认」提拔成「已确认」。那是确认环节的权力。
"""

from __future__ import annotations

import re
import sqlite3

from scaffold import ScaffoldError, activate, add_artifact, get, record_event
from scaffold import add_relation, content_of, heads_of, revise

# 抓 SQL 里的表名。笨正则，刻意的 —— 见 `signals_read()`。
_FROM_LIKE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_]*)",
                        re.IGNORECASE)

# 换规则必须改版本号 —— 新旧排序不会长得一样（`§C5.5` 第 5 条同一条要求）。
INDUCER_VERSION = "upper-context/1"

# `§C7.1` ① 的信号白名单。**只有这些**。
#
# 键是内部名，值是它读的那张表 / 那个 event kind。
# 这张表存在的意义是**可被检查**：B14 会拿它去比对模块里出现的表名 / 列名，
# 出现白名单之外的（尤其 `vote` / `score` / `views`）就报命中。
COUNT_SIGNALS = {
    "challenge_counts": "relation:challenged_by",   # 某个假设被反复质询
    "revision_counts": "revision",                  # 某条关系 / 节点被反复修正
    "dispute_counts": "relation:contradicts",       # 某类争议反复出现
}

# ⚠️ **热度的类信号一个都不许读**（不变量 #5：Popularity ≠ Evidence）。
#
# 守卫在 `checks.py` 的 **B14**（那份文件是 `EXEMPT` 的，可以明写禁词）。
# 这里不列禁词，理由很实在：B2 是 dumb 子串扫描，
# **我在这张文件里把禁词写出来，等于自己撞上去** —— 已实测撞过一次。
# 所以这里只放「允许读的表」，B14 拿它去比对本模块 SQL 里出现的表名。
READABLE_TABLES = ("relation", "artifact", "revision", "event")

# 节点名留空时的值。**不是占位符** —— 见模块开头「命名」那一节。
PENDING_NAME = "（待命名）"

# 上层节点建在这个 type 下。`Context` 是本模块自选的（`§C4` 未规定上层节点类型），
# 走 `§T4` 留白项：声明 + 可回退，不新增争议。
UPPER_TYPE = "Context"

# 上层的边。**不是底层的真值边** —— 它只表达「这一簇属于这个候选上下文」。
# 删掉它，底层一字不少（单向性约束的可执行形式）。
UPPER_RELATION = "clustered_into"

MIN_SUPPORT = 2
"""一个候选要成立，至少得有几条底层依据。

**这不是阈值判据，是一个下限。** 区别：阈值是「过了这条线就算重要」——
那是个判断，`§T0.3` 不许我定；下限是「一条依据的簇不叫簇」——
那是「这一簇是否成立」的结构问题，不是「它重不重要」。
`§C7.1` ① 推荐定义（跨群共识）里也有同一个结构要求：**要求同时满足所有群**，
单群不成立。所以 2 是这条要求的 MVP 版本，不是一条我拍的线。
"""


def count_signals(conn: sqlite3.Connection) -> dict:
    """`§C7.1` ① 白名单里的三个结构量。**只读，不读任何热度类的东西。**

    返回的是**原料**，不是名次：谁被质询了多少次。
    「多少次算多」不在这里判 —— 那是排序，排序在 `propose_clusters()` 里
    按**依据条数**出，而依据条数是结构事实，不是分数。
    """
    out: dict[str, dict] = {}

    # ① 某个假设被反复质询的次数。
    # ⚠️ 方向：`claim ──challenged_by──> carg`（`§C4` 的 Claim → Counterargument）。
    # 所以「被质询的那个东西」是 **from_id**。第一版写成 to_id，数出来全是
    # 一条条的 Counterargument（每个正好 1 次），候选永远是 0 —— 见 DECLARATION §22.3。
    rows = conn.execute(
        "SELECT r.from_id AS target, COUNT(*) AS n FROM relation r"
        " WHERE r.kind = 'challenged_by' AND r.state = 'active'"
        " GROUP BY r.from_id ORDER BY r.from_id",
    ).fetchall()
    out["challenge_counts"] = {r["target"]: r["n"] for r in rows}

    # ② 某条关系被反复修正的次数（revision 表是 append-only，见 §C10）
    rows = conn.execute(
        "SELECT artifact_id, COUNT(*) AS n FROM revision"
        " GROUP BY artifact_id HAVING COUNT(*) > 1 ORDER BY artifact_id",
    ).fetchall()
    out["revision_counts"] = {r["artifact_id"]: r["n"] for r in rows}

    # ③ 某类争议反复出现的次数。方向同 ①：被反复反驳的是 **from_id**。
    rows = conn.execute(
        "SELECT r.from_id AS target, COUNT(*) AS n FROM relation r"
        " WHERE r.kind = 'contradicts' AND r.state = 'active'"
        " GROUP BY r.from_id ORDER BY r.from_id",
    ).fetchall()
    out["dispute_counts"] = {r["target"]: r["n"] for r in rows}

    return out


def signals_read(source: str) -> list[str]:
    """本模块 SQL 里读到的表名，**减去白名单** —— 多出来的就是越界信号。

    给 `checks.py` 的 B14 用，也让 `scan()` 能自述它到底读了什么表。
    实现刻意做得很笨：正则抓 `FROM x` / `JOIN x` / `UPDATE x` / `INTO x`。
    笨是特性 —— 聪明版本要维护 SQL 解析，而这个函数的全部价值
    就是「它不会被绕过得太容易」。
    """
    names = set(_FROM_LIKE.findall(source))
    return sorted(n for n in names if n and n not in READABLE_TABLES)


def propose_clusters(conn: sqlite3.Connection) -> dict:
    """按 `§C7.1` 的白名单信号，提议上层节点。**只读，一个字都不写。**

    产出的是**候选清单**（`§C7.1` ④：「它改变的是『什么值得被看见』的
    候选清单，不是『什么是对的』」）。要不要真建，由调用方决定 ——
    建的时候走 `promote_candidates()`。

    为什么本函数不直接建：提议是**高频**动作，建是**低频**动作
    （`§C7.1` ③ 的理由）。混在一起的话，每次重算都会多出一批节点，
    而「不得不清理自动产出的垃圾」正是让人开始乱删结构的起点。
    """
    sig = count_signals(conn)
    candidates = []

    for signal_key, counts in sig.items():
        for target, n in counts.items():
            if n < MIN_SUPPORT:
                continue
            node = get(conn, target)
            claims = _supporting_ids(conn, target, signal_key)
            if len(claims) < MIN_SUPPORT:
                continue
            candidates.append({
                "signal": signal_key,
                # ⚠️ 依据是**已有节点的 id**，不是系统写的句子。
                # 这一点是本模块能免确认的全部理由。
                "evidence_ids": claims,
                "seed_id": target,
                "seed_type": node["type"],
                "n": n,
                # 名字**不由系统给**。这里给的是「拿依据里哪些原话可以当名字参考」，
                # 不是名字本身。
                "name_source": "human",
                "name": PENDING_NAME,
            })

    # 按**依据条数**排（结构事实），不按 signal 名排。
    # ⚠️ 这不是名次：它决定的是「默认展开顺序」，那是 `§C7.1` ④
    # 明确允许上层影响的三种东西之一。
    candidates.sort(key=lambda c: (-len(c["evidence_ids"]), c["seed_id"]))
    return {
        "version": INDUCER_VERSION,
        "count_signals": sig,
        "candidates": candidates,
        "note": (
            "这些是**候选**，不是结论。它们只影响「什么排在前面 / 默认展开什么」，"
            "不得写进底层任何字段（`§C7.1` ④）。"
            "节点名由人来给 —— 系统一个字都没说。"
        ),
    }


def _supporting_ids(conn: sqlite3.Connection, target: str, kind: str) -> list[str]:
    """一个候选的底层依据 —— **已经 `active` 的节点 id**。

    ⚠️ 这里只**读**。`active` 是确认环节授予的（`§C2.5` 第 2 档），
    上层归纳不许把任何东西从「未确认」提拔成「已确认」——
    那是本模块最危险的那种越权，`promote_candidates()` 里有硬检查拦它。
    """
    if kind == "challenge_counts":
        # 质询它的那些 Counterargument —— 方向是 `target ──challenged_by──> carg`。
        rows = conn.execute(
            "SELECT r.to_id FROM relation r JOIN artifact a ON a.id = r.to_id"
            " WHERE r.from_id = ? AND r.kind = 'challenged_by' AND r.state = 'active'"
            "   AND a.state = 'active' ORDER BY r.to_id",
            (target,),
        ).fetchall()
    elif kind == "dispute_counts":
        # 反驳它的那些节点 —— 方向是 `target ──contradicts──> 对方`。
        rows = conn.execute(
            "SELECT r.to_id FROM relation r JOIN artifact a ON a.id = r.to_id"
            " WHERE r.from_id = ? AND r.kind = 'contradicts' AND r.state = 'active'"
            "   AND a.state = 'active' ORDER BY r.to_id",
            (target,),
        ).fetchall()
    elif kind == "revision_counts":
        # 修正记录挂在被改的那个 artifact 自己身上（revision.artifact_id）。
        rows = conn.execute(
            "SELECT DISTINCT artifact_id AS id FROM revision"
            " WHERE artifact_id = ? ORDER BY artifact_id",
            (target,),
        ).fetchall()
    else:
        raise ScaffoldError(
            f"{kind!r} 不在 `§C7.1` 的允许信号里（见 COUNT_SIGNALS）。"
            "要加信号先改那张表 —— 那是一次要留痕的动作，不是顺手能加的。"
        )
    return [r[0] for r in rows]


def promote_candidates(
    conn: sqlite3.Connection, *, candidates: list[dict], by: str,
    debate_id: str | None = None,
) -> list[str]:
    """把候选**建成节点**。节点名留空（`PENDING_NAME`），等一个真人来起。

    这里写的只有三样：

    1. 一个 `Context` artifact，内容 `{"text": PENDING_NAME, "name_source": "human"}`
    2. 一条 `Relevance contains Context` 边，从 Debate（或它的 Topic）挂过去
    3. 一条 `clustered_into` 边，从每个依据节点指向这个 Context

    **一个字都没写进底层节点的 content。** 底层节点的 `content` / `state` /
    `revision` 全程只读 —— 这是单向性约束的可执行形式。
    """
    made = []
    for c in candidates:
        # ⚠️ 硬检查之一：依据必须**已经 active**。
        # 上层归纳不许提拔任何东西 —— 那是确认环节的权力（`§C2.5` 第 2 档）。
        for eid in c["evidence_ids"]:
            if get(conn, eid)["state"] != "active":
                raise ScaffoldError(
                    f"{eid} 还不是 active。上层归纳不许把未确认的东西提拔成已确认 —— "
                    "那是确认环节的权力（`§C2.5` 第 2 档 / `§C7.1` ④）。"
                )

        node_id = add_artifact(
            conn, type_=UPPER_TYPE,
            content={
                # ⚠️ 名字**不由系统给**。这一条不是风格问题，是本模块能免确认的全部理由：
                # 名字留空，系统就一句话都没说（见模块开头「命名」那一节）。
                "text": PENDING_NAME,
                "name_source": "human",
                "signal": c["signal"],
                "evidence_ids": c["evidence_ids"],
                "induction_version": INDUCER_VERSION,
            },
            origin=by,
        )
        # ⚠️ 建完**立刻 activate**，理由要说清楚，因为它和「必须确认」看着冲突。
        #
        # 上层节点不是一条新断言 —— 它是**底下那几条已有命题的一条归组边**。
        # 它之所以能免确认，是因为名字留空：系统**一句话都没说**（见模块开头）。
        # 既然系统没说话，「等谁确认」就没有对象 —— 它不 pending 任何待批的断言。
        #
        # 往下压一层说：`state` 管的是「这条断言有没有被确认」，
        # 而本节点的「待定」是**「还没起名」**—— 那是另一种东西，
        # 由 `name_source` + `PENDING_NAME` 表达（见 `rename_context`）。
        # **把这两种「待定」压进同一个字段，就等于说「未命名 = 未确认」**，
        # 而那恰好会把「免确认」这条设计的理由抹掉。
        #
        # 若把它留成 `proposed`：`ordered_view` 这类消费方会把它当成
        # 「一条还没被批准的断言」—— 上层结果就被降格成了待批提案，
        # 而它本来就是允许影响展示顺序的（`§C7.1` ④）。
        activate(conn, node_id, by=by)
        anchor = debate_id or _anchor_of(conn, c["seed_id"])
        if anchor:
            add_relation(conn, kind="contains", from_id=anchor, to_id=node_id,
                         origin=by)
        else:
            # ⚠️ 没有锚点时**不静默吞掉**。悬空的上层节点任何 `ordered_view`
            # 都到不了它 —— 它存在、占着 id、却谁也看不见。那正是 `debate.py`
            # 里 `open_topic()` 警告过的那种错（「悬空的 Topic 任何 view 都到不了」）。
            # 这里不抛错（调用方可能正打算稍后补挂），但**必须留一笔**，
            # 否则失败长得跟成功一模一样。
            record_event(conn, "upper_context_unanchored", by, node_id,
                         {"seed_id": c["seed_id"]})
        for eid in c["evidence_ids"]:
            add_relation(conn, kind=UPPER_RELATION, from_id=eid, to_id=node_id,
                         origin=by)
        made.append(node_id)

    record_event(conn, "upper_context_proposed", by, debate_id,
                 {"created": made, "version": INDUCER_VERSION,
                  "signals": sorted({c["signal"] for c in candidates})})
    conn.commit()
    return made


def _anchor_of(conn: sqlite3.Connection, artifact_id: str) -> str | None:
    """顺着 contains 往上找开会话 —— 上层节点挂在 Debate 下，不散在库里。"""
    seen: set[str] = set()
    cur = artifact_id
    for _ in range(8):                      # 环保护：结构有 bug 时不要死循环
        row = conn.execute(
            "SELECT r.from_id FROM relation r JOIN artifact a ON a.id = r.from_id"
            " WHERE r.to_id = ? AND r.kind = 'contains' AND r.state = 'active'"
            "   AND a.type = 'Debate' LIMIT 1",
            (cur,),
        ).fetchone()
        if row:
            return row["from_id"]
        nxt = conn.execute(
            "SELECT r.from_id FROM relation r"
            " WHERE r.to_id = ? AND r.kind = 'contains' AND r.state = 'active'"
            " LIMIT 1",
            (cur,),
        ).fetchone()
        if not nxt or nxt["from_id"] in seen:
            return None
        seen.add(cur)
        cur = nxt["from_id"]
    return None


def rename_context(
    conn: sqlite3.Connection, *, context_id: str, name: str, by: str,
) -> int:
    """给上层节点起名。**这是人做的动作，不是系统的。**

    走 `scaffold.revise` —— 不覆盖（`§C10`）。旧名留在 revision 表里，
    所以「这个名字改过几次、谁改的」查得到。`name_source` 改成 `human`，
    与 `PENDING_NAME` 区分开：**没起过名的节点，一眼能认出来。**

    ⚠️ **改名只改名字，其余字段原样带走。**
    `revise()` 换的是**整个 content**（那是对的 —— 它是通用版本接口），
    所以这里必须**读-改-写**：只换 `text`，`signal` / `evidence_ids` /
    `induction_version` 原封不动带过去。

    **实测踩过这个坑**：第一版只传了 `{"text": name, "name_source": "human"}`，
    结果改完名之后节点上的 `signal` 变成 `None`、`evidence_ids` 变成 `[]` ——
    **依据全丢了**，而视图照样正常印出节点的名字，看起来完全没问题。
    这正是「坏起来不像坏」：一个上层节点改名之后，就再也说不清
    「它当初是根据什么长出来的」。
    """
    node = get(conn, context_id)
    if node["type"] != UPPER_TYPE:
        raise ScaffoldError(f"{context_id} 是 {node['type']}，不是 {UPPER_TYPE}。")
    if not (name or "").strip():
        raise ScaffoldError("名字不能为空。")
    if name.strip() == PENDING_NAME:
        raise ScaffoldError(
            f"{PENDING_NAME!r} 是「还没起名」的标记，不是名字。"
            "要表达不同意见走 revise，别拿它当名字。"
        )
    # 读-改-写：只换 text，其余字段原样带过去（见 docstring 里那个实测的坑）
    content = dict(content_of(conn, context_id))
    content["text"] = name.strip()
    content["name_source"] = "human"
    rev = revise(conn, context_id, content=content, author=by)
    record_event(conn, "upper_context_named", by, context_id, {"revision": rev})
    conn.commit()
    return rev


def ordered_view(conn: sqlite3.Connection, debate_id: str) -> dict:
    """`§C7.1` ④ 允许上层影响的**全部三种东西**：展示顺序 / 默认展开 / 推荐候选。

    超出这三样的，本层的视图里没有位置。
    """
    rows = conn.execute(
        "SELECT a.* FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = ? ORDER BY a.id",
        (debate_id, UPPER_TYPE),
    ).fetchall()
    items = []
    for r in rows:
        c = content_of(conn, r["id"])
        items.append({
            "context": dict(r),
            "name": c.get("text", PENDING_NAME),
            "named": c.get("name_source") == "human" and c.get("text") != PENDING_NAME,
            "signal": c.get("signal"),
            "evidence_ids": c.get("evidence_ids", []),
            "history": [dict(h) for h in heads_of(conn, r["id"])],
        })
    return {
        "debate": debate_id,
        "contexts": items,
        "note": (
            "上层只影响三件事：**展示顺序 / 默认展开 / 推荐候选**。"
            "它没有说任何一条命题「对」，也一个字都没写进底层（`§C7.1` ④）。"
        ),
    }


def render(conn: sqlite3.Connection, debate_id: str) -> str:
    """印成人看的。只读 —— 和 `hints.scan()` / `observe.snapshot()` 一个规矩。"""
    v = ordered_view(conn, debate_id)
    lines = [
        "=" * 64,
        f"上层上下文 {debate_id}",
        f"  规则集 {INDUCER_VERSION} · `§C7.1`（MVP 阶段本机制按留白项处理）",
        "",
    ]
    if not v["contexts"]:
        lines += [
            "  还没有上层节点。",
            "",
            "  ⚠️ 「还没有」和「算不出来」不是一回事，所以两句都印：",
            "     · 还没有 —— 底层的结构性信号还没攒够（同一条假设至少被质询 2 次）",
            "     · 算不出 —— 本模块看不见某种信号（见 BLIND_SPOTS）",
            "",
        ]
    for c in v["contexts"]:
        flag = "" if c["named"] else "   ← 名字由人来给（系统一个字都没说）"
        lines.append(f"  {c['context']['id']}  「{c['name']}」{flag}")
        lines.append(f"      信号：{c['signal']}   依据 {len(c['evidence_ids'])} 条："
                     f"{c['evidence_ids']}")
        lines.append("")
    lines += [
        "⚠️ 上层只影响：展示顺序 / 默认展开 / 推荐候选。",
        "   它没有说任何一条命题「对」，也一个字都没写进底层（`§C7.1` ④）。",
    ]
    return "\n".join(lines)


BLIND_SPOTS = (
    "只看 `challenged_by` / `contradicts` / `revision` 三种结构量",
    "看不见「没被质询但也没人支持」的东西 —— 那是沉默，不是信号",
    "看不见跨语言、跨表述的同一观点（不做文本相似度，见模块开头）",
)


def scan(conn: sqlite3.Connection, debate_id: str) -> dict:
    """同 `hints.scan()` 的规矩：**空结果永远附一句话**，说清扫了什么、认不出什么。

    这是 `observe.py`「算不出 ≠ 零」的同一条要求：
    **没测到的东西，不许长得像测到了零。**
    """
    v = ordered_view(conn, debate_id)
    return {
        "debate": debate_id,
        "version": INDUCER_VERSION,
        "contexts": v["contexts"],
        "empty_reason": (
            None if v["contexts"]
            else "底层还没有攒够结构性信号（同一条依据至少 2 条）"
        ),
        "blind_spots": list(BLIND_SPOTS),
        "note": v["note"],
    }


__all__ = [
    "INDUCER_VERSION", "COUNT_SIGNALS", "READABLE_TABLES", "PENDING_NAME",
    "UPPER_TYPE", "UPPER_RELATION", "MIN_SUPPORT", "BLIND_SPOTS",
    "count_signals", "propose_clusters", "promote_candidates",
    "rename_context", "ordered_view", "render", "scan", "signals_read",
]
