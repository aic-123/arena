"""Scaffold 最小层 —— 对象与关系（`§C3`）。

本文件只做三件事的存储与访问：**Artifact / Relation / Revision**。
Evidence 与 Challenge 不另建机制 —— 按 `§C3.3` `§C3.4`，它们是
「Artifact 的类型」+「Relation 的组合」。

--- 硬约束：违反即实现错误 -------------------------------------------------

`§C3.2`  Relation 是**可追踪对象**（有身份、有来源、可追溯），不是外键字段。
          → relation 是独立表、有自增身份、有 origin、有 state、能被 rejected。
`§C10`   禁止覆盖式更新。每个版本一行，**只增不改**。
          → revision 表没有 UPDATE 路径；并发修改自然形成分叉（多个 head）。
#3        机器产出不得直接成为 verified knowledge。
          → `verified` 只有 `grant_verified()` 一条写入路径，且必须带 hook 名。
`§C2.4`   每条 AI 产出的结构边：可追溯 / 可拒绝 / 可修改 / 可计量。
          → relation 有 origin(追溯) / state(拒绝) / superseded_by(修改) / 表本身可计量。
分层不变量 #11
          本文件**不得出现任何讨论行为**（确认、投票、质询、分裂）。
          那些属于 Arena 层。本文件不认识「用户」「投票」「分歧」这些概念。

--- 反向约束（本文件刻意不做的事）------------------------------------------

- 不排序、不聚合、不打分。任何评分 / 权重 / 名次字段都不存在（`§C6.1` `§C9` #5 #7）。
- 不加锁。`§C11.2` 要求先让真实并发把问题打出来，再决定引入哪些机制。
- 不校验内容对错。只保证结构自洽（借鉴参考实现「校验器不判内容」的立场）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 权威常量
# ---------------------------------------------------------------------------

# `§C3.1` 的 Artifact 类型清单，取并集 —— 见 DECLARATION.md §4「文档冲突的处理」。
# `§C3.1` 列了 7 类，但 `§C4` 的 Debate 结构树用到了 Mechanism / Assumption /
# Counterexample / Counterargument，`§C3.4` 又要求 Challenge 是 Relation + Artifact 的组合。
# 所以 `§C3.1` 当**下界**读，不当闭集读。
ARTIFACT_TYPES = (
    "Topic",           # §C3.1
    "Position",        # §C4 —— §C3.1 的枚举漏了它，见下方注释
    "Claim",           # §C3.1
    "Evidence",        # §C3.1
    "Argument",        # §C3.1
    "Debate",          # §C3.1
    "Vote",            # §C3.1
    "Subtopic",        # §C3.1
    "Mechanism",       # §C4
    "Assumption",      # §C4
    "Counterexample",  # §C4
    "Counterargument", # §C4
    "Challenge",       # §C3.4
    "Context",         # §C7.1 —— 上层节点，见下方注释
)

# ⚠️ `Context` 是**上层**节点（`§C7.1` 的 Context Scaffold），
# 与上面那些**底层**类型有一条硬边界，不能混：
#
#     底层（Topic/Position/Claim/Evidence…）  记事实。确认环节授予 active（§C2.5）
#     上层（Context）                        只派生。**不得写底层任何字段**（§C7.1 ④）
#
# 它进这张表的理由与 `Position` 不同 —— `Position` 是 `§C4` 画漏了；
# `Context` 是 `§C4` **没规定**（`§C7.1` 只说了机制，没说节点叫什么类型），
# 所以走 `§T4` 的留白项：自选 + 声明 + 可回退。它不在任何文档的枚举里，
# **这一点必须说清**，别让它看起来像 `§C3.1` 漏了一个。

# ⚠️ `Position` 不在 `§C3.1` 的枚举里，但 `§C4` 的结构树**第一行就用它**：
#
#     Topic
#     ├── Position / Claim        ← 这一行
#     │   ├── Evidence ...
#     └── Opposing Claim
#
# 与 `Mechanism` / `Assumption` / `Counterexample` / `Counterargument` 属于**同一类漏项**
# —— 那几个也不在 `§C3.1` 里，而 `§C4` 的树全用到了。既有的处理是
# 「把 `§C3.1` 当**下界**读，不当闭集读」（DECLARATION §5），此处沿用同一条，
# 不新增争议：它和那四个是同一个判决，不是一次新的放宽。
#
# `§C4` 那句「必须逐条落地，不可合并」针对的是**关系映射表**，
# 但树的形状本身也是结构要求 —— `Position` 是树上一个确定的层
# （需求方 2026-09-27 明确要求「论点一层」，并认可二元对立暂代多立场）。

# `§C3.2` 的六种，并上 `§C4` / `§C6.3` 要求但未列入的四种。同上，取并集。
RELATION_KINDS = (
    "contains",        # §C3.2
    "derived_from",    # §C3.2
    "supports",        # §C3.2 · §C4 · §C6.3
    "contradicts",     # §C3.2 · §C4 · §C6.3
    "refines",         # §C3.2
    "related_to",      # §C3.2 · §C8.2
    "qualifies",       # §C4 · §C6.3 —— 不在 §C3.2 的六种里
    "assumes",         # §C4  Claim → Assumption
    "explains",        # §C4  Claim → Mechanism
    "challenged_by",   # §C4  Claim → Counterargument
    # ---- 缺口①（需求方样本标注暴露，2026-09-25）----------------------------
    # 「A 导致 B」这个命题与它两端的关系。原来没有种类，`confirm.py` 拿
    # `related_to` 兜住 —— 而 `related_to` 恰恰**丢失因果方向**，等于没表达。
    # 文件里写的是「需要**一类**…请定名」。这里给了**两种**，理由：
    # 一种关系做不到「同一端既可能是因、又可能是果」都要能说
    #   B1  n1（强度大）是 n3 的**因**
    #   B4  n1（成绩好）是 n2 的**果**
    # 方向是这一族关系唯一的载荷，一种就等于退回 `related_to`。
    # 方向照文件自己的箭头：**端 → 因果主张**（B1 写的是 `n1 ─?→ n3`）。
    # 名称用文件给的两个词：前提 / 结论。
    "causal_premise",    # 缺口①  因 → 因果主张（「这条是该因果主张的前提」）
    "causal_conclusion", # 缺口①  果 → 因果主张
    # ---- `§C7.1` 上层（Context Scaffold）—— 需求方 2026-09-27 ----------------
    # ⚠️ **这一条与前两种性质不同，必须分清**：前两种是底层边（记事实），
    # 这一条是**上层边**（只派生）。它不是底层的真值边 —— 整批删掉，
    # 底层一字不少。`§C7.1` ④ 的单向性约束就落在这条边不许反向。
    #
    # 名字取「被并进某个上下文」的意思，不取「相似于」：
    # 「相似」是一种距离判断，而本层只用结构量（见 upper.py 模块开头）。
    "clustered_into",    # §C7.1  底层节点 → 上层 Context
)

# Artifact 生命周期（`§C3.1`「具有独立身份、状态、生命周期」）。
# 唯一能写 active 的路径是 Arena 层的用户确认 —— 见 arena/debate.py。
ARTIFACT_STATES = ("proposed", "active", "superseded")

# Relation 状态（`§C2.4` 可拒绝 / 可修改）。
# 机器产出的边默认 `active` —— 这是 `§C2.5` 的「派生标注：默认生效 + 可推翻」，
# **不是**「默认正确」。它随时可以被 rejected，改判率就是从这里算的。
RELATION_STATES = ("active", "rejected", "superseded")

# Evidence 状态（`§C12.2` 的 Evidence Status 一栏）。
# `§C9` #3：AI 生成内容不得直接成为 verified knowledge。
STATUS_DEFAULT = "unresolved"
STATUS_HOOK_ONLY = "verified"

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


class ScaffoldError(Exception):
    """结构层拒绝了一次写入。消息面向调用方，说明违反了哪一条。"""


# ---------------------------------------------------------------------------
# 建库
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact (
    id          TEXT PRIMARY KEY,          -- 独立身份（§C3.1）
    type        TEXT NOT NULL,             -- §C3.1 类型清单
    state       TEXT NOT NULL,             -- 生命周期
    status      TEXT NOT NULL,             -- Evidence 状态；默认 unresolved
    origin      TEXT NOT NULL,             -- 产生者，可追溯（§C2.4 / #9）
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revision (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    parent_rev  INTEGER,                   -- NULL=根；并发修改产生多个子 → 分叉（§C10）
    content     TEXT NOT NULL,             -- JSON：规范化文本 + 结构化字段
    author      TEXT NOT NULL,             -- 谁改的（§C7.2「谁改了什么」）
    created_at  TEXT NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifact(id)
);

CREATE TABLE IF NOT EXISTS relation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,           -- §C3.2 的六种 ∪ §C4 的四种
    from_id       TEXT NOT NULL,
    to_id         TEXT NOT NULL,
    origin        TEXT NOT NULL,           -- 可追溯：这条边是谁产的
    state         TEXT NOT NULL,           -- 可拒绝：active / rejected / superseded
    superseded_by INTEGER,                 -- 可修改：被哪条 relation 取代
    created_at    TEXT NOT NULL,
    FOREIGN KEY (from_id) REFERENCES artifact(id),
    FOREIGN KEY (to_id)   REFERENCES artifact(id)
);

-- 观测点的事实记录（`§C7.2`）。
-- ⚠️ 这里只记**事实**（谁、何时、做了什么），不记任何评估结果。
-- 「换说法率」「改判率」等比率是**上层派生视图**，从这张表算出来，
-- **不得**反向写成 artifact 的字段 —— 那是 §C7.1 ④ 禁止的单向性违反。
CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    actor       TEXT NOT NULL,
    subject_id  TEXT,
    payload     TEXT NOT NULL,             -- JSON
    created_at  TEXT NOT NULL
);

-- 身份计数器（`§T2` 第 12 步引入）。
-- 为什么是**一张表**而不是加锁：见 `next_seq()` 的说明。
-- 它只存「发到几号了」这一个数，不参与任何讨论语义 —— 所以它属于 Scaffold 层。
CREATE TABLE IF NOT EXISTS seq (
    name        TEXT PRIMARY KEY,
    n           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rev_artifact ON revision(artifact_id);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relation(from_id);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relation(to_id);
CREATE INDEX IF NOT EXISTS idx_event_kind ON event(kind);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# 身份
# ---------------------------------------------------------------------------

_PREFIX = {
    "Topic": "topic", "Position": "pos", "Claim": "claim", "Evidence": "evid",
    "Argument": "arg", "Debate": "debate", "Vote": "vote",
    "Subtopic": "sub", "Mechanism": "mech", "Assumption": "assume",
    "Counterexample": "cex", "Counterargument": "carg", "Challenge": "chal",
    "Context": "ctx",      # §C7.1 上层节点 —— 前缀独立，一眼分出上下层
}


def next_seq(conn: sqlite3.Connection, name: str) -> int:
    """**原子地**取下一个序号。全系统发身份只有这一条路。

    --- 为什么有它（`§T2` 第 12 步：先暴露，再引入机制）--------------------

    原来的写法是「**数一行数，再拼一个名字**」：`SELECT COUNT(*)` → `f"{p}-{n+1}"`。
    那是**两步**，两个进程会在中间那段缝里算出同一个 id：
    一个写成功，另一个 `IntegrityError: UNIQUE constraint failed`。
    不是缝有多宽的问题 —— 这个写法**在任何宽度下都是错的**。

    --- 换掉的是写法，不是加了把锁 -----------------------------------------

    `UPDATE ... RETURNING` 由 SQLite 自己保证「读-改-写」不可分割，
    所以那条缝**根本不存在**了，而不是被锁盖住。
    于是 `§C11.3` 禁止的分布式锁、消息队列、Redis 一个都没引，零新依赖。

    这也正是判据表要的顺序：**先让真并发把它打出来（第 10/11 步），
    再引入成熟方案（第 12 步）**。没有第 11 步那条记录，这个改动就是凭空的。

    --- 两点行为变化，明说 -------------------------------------------------

    1. **号是「领」走的，不是「数」出来的**：调一次就前进一格，
       领了不用就留个空号。以前是纯函数式的（同状态给同一个答案），
       现在不是 —— 这是它换来的原子性的代价，也是它唯一可能的形态。
    2. **并发下的编号不再可预测**（谁先领谁拿小号）。
       编号本来就只是身份，不是顺序也不是名次（`§C9` #5），所以这不损失什么；
       但**顺序跑仍然是逐号递增的**，从空库跑到链尾依旧可复现。

    3. ⚠️ **领号会开一个写事务，调用方必须提交。**
       这是第 14 步加压时才发现的一条**新契约**，而它现在**没有任何地方强制**：
       老写法是纯 `SELECT`，只拿读锁、谁都不挡；新写法拿的是**写锁**，
       所以在「领了号」到「commit」之间，**整个库别的写者都进不来**。
       真实调用路径（`add_artifact` / `_insert_draft` → `propose`）都是领完马上提交，
       实测 8 进程 × 12 轮零异常；但**如果将来有人在 `new_id()` 之后忘了提交，
       症状会是别人那边 `database is locked`**，而且看起来跟发号器毫无关系。
       这条契约只在文档里，是靠不住的 —— 记在这里，先不当成已解决。

    `INSERT OR IGNORE` 只用来播种一行计数器：名字是主键、值恒为 0，
    所以「忽略冲突」丢不掉任何数据 —— 它只会跳过重复播种。
    """
    conn.execute("INSERT OR IGNORE INTO seq (name, n) VALUES (?, 0)", (name,))
    row = conn.execute(
        "UPDATE seq SET n = n + 1 WHERE name = ? RETURNING n", (name,)
    ).fetchone()
    return int(row["n"])


def new_id(conn: sqlite3.Connection, type_: str) -> str:
    """`前缀-四位序号`。同一类型内递增，从空库跑到链尾时可复现。

    序号怎么来的见 `next_seq()` —— 那是这一步唯一改掉的东西。
    **对外的样子一个字没变**：`claim-0001` 还是 `claim-0001`。
    """
    if type_ not in ARTIFACT_TYPES:
        raise ScaffoldError(f"未知 Artifact 类型：{type_}（见 §C3.1 ∪ §C4）")
    return f"{_PREFIX[type_]}-{next_seq(conn, type_):04d}"


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def add_artifact(
    conn: sqlite3.Connection,
    *,
    type_: str,
    content: dict,
    origin: str,
    state: str = "proposed",
) -> str:
    """建一个 Artifact，并落它的第 1 个 revision。

    `state` 默认 `proposed` —— 机器产出的东西先落这里。
    转 `active` 只有 Arena 层的用户确认那一条路（`§C5`：未经确认不得写入 Scaffold）。
    """
    if state not in ARTIFACT_STATES:
        raise ScaffoldError(f"未知 state：{state}")
    if state == "active":
        raise ScaffoldError(
            "add_artifact 不得直接建 active 对象。"
            "active 只能由用户在确认环节授予（§C5「未经确认不得写入 Scaffold」）。"
        )
    aid = new_id(conn, type_)
    now = _now()
    conn.execute(
        "INSERT INTO artifact (id, type, state, status, origin, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (aid, type_, state, STATUS_DEFAULT, origin, now),
    )
    _append_revision(conn, aid, None, content, origin)
    conn.commit()
    return aid


def get(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM artifact WHERE id = ?", (artifact_id,)
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"不存在的 Artifact：{artifact_id}")
    return row


def activate(conn: sqlite3.Connection, artifact_id: str, *, by: str) -> None:
    """proposed → active。**唯一**入口，且必须写明是谁确认的。"""
    row = get(conn, artifact_id)
    if row["state"] != "proposed":
        raise ScaffoldError(
            f"{artifact_id} 当前是 {row['state']}，只有 proposed 能被确认。"
        )
    conn.execute(
        "UPDATE artifact SET state = 'active' WHERE id = ?", (artifact_id,)
    )
    record_event(conn, "artifact_confirmed", by, artifact_id, {"type": row["type"]})
    conn.commit()


# ---------------------------------------------------------------------------
# Revision（§C10：记录，不停止，不限制）
# ---------------------------------------------------------------------------

def _append_revision(
    conn: sqlite3.Connection, artifact_id: str,
    parent_rev: int | None, content: dict, author: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO revision (artifact_id, parent_rev, content, author, created_at)"
        " VALUES (?,?,?,?,?)",
        (artifact_id, parent_rev, json.dumps(content, ensure_ascii=False), author, _now()),
    )
    return int(cur.lastrowid)


def revise(
    conn: sqlite3.Connection, artifact_id: str, *,
    content: dict, author: str, parent_rev: int | None = None,
) -> int:
    """加一个新版本。**不覆盖旧版本** —— 旧版本一行都不会被改。

    `parent_rev=None` 时挂到当前 head。若此时有两个 head（已分叉），
    必须显式指定 `parent_rev` —— 系统不会替你挑一个，
    因为挑一个就是替用户做了一个判断。
    """
    heads = heads_of(conn, artifact_id)
    if parent_rev is None:
        if len(heads) > 1:
            raise ScaffoldError(
                f"{artifact_id} 已有 {len(heads)} 个分支 "
                f"({[h['id'] for h in heads]})，必须显式指定 parent_rev。"
                "系统不替你选分支 —— 那是一次判断。"
            )
        parent_rev = heads[0]["id"] if heads else None
    rev = _append_revision(conn, artifact_id, parent_rev, content, author)
    record_event(conn, "artifact_revised", author, artifact_id, {"revision": rev})
    conn.commit()
    return rev


def history_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """一个 Artifact 的完整版本谱系（`§C10` 的目标形态：Current State + Revision History）。

    **全部版本一条不略**（「记录，不停止，不限制」），按 id —— 也就是录入顺序，
    不是「最新最前」那种名次。

    分叉点与多头都标出来，而且**不替你挑**：
    多头时 `current` 是 `None`，不是任选一个。挑一个就是替用户做了一次判断 ——
    与 `revise()` 在多头时拒绝自动挂版本是同一个行为（`§C10`）。

    `§C10` 末段：用户**不能**删除已发送的话。所以这里没有、也不会有删除入口。
    """
    rows = conn.execute(
        "SELECT * FROM revision WHERE artifact_id = ? ORDER BY id", (artifact_id,)
    ).fetchall()
    if not rows:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")

    children: dict[int, list[int]] = {}
    for r in rows:
        if r["parent_rev"] is not None:
            children.setdefault(r["parent_rev"], []).append(r["id"])
    heads = [r["id"] for r in rows if not children.get(r["id"])]
    forks = [rid for rid, kids in children.items() if len(kids) > 1]

    if len(heads) == 1:
        note = f"当前状态是第 {heads[0]} 版；此前 {len(rows) - 1} 版全部保留，一条没删。"
    else:
        note = (
            f"这里有 {len(heads)} 个分支：{heads}。"
            "**系统不替你挑一个** —— 哪一版算数要人来看。"
            "两版都没有被丢弃（`§C10`：记录，不停止，不限制）。"
        )
    return {
        "artifact": artifact_id,
        "revisions": [
            {"id": r["id"], "parent_rev": r["parent_rev"], "author": r["author"],
             "created_at": r["created_at"], "content": json.loads(r["content"]),
             "is_head": r["id"] in heads}
            for r in rows
        ],
        "heads": heads,
        "branch_points": forks,
        "current": heads[0] if len(heads) == 1 else None,
        "note": note,
    }


def content_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """当前版本的 content。**多头时拒绝挑一个**（`§C10`）。

    要读「用户当初提交的那一段」用 `original_content_of()`；
    要读「现在算哪一版」用这个，且它会在分叉时直接拒绝 ——
    那不是报错，那是**没有当前版本**这件事本身。
    """
    heads = heads_of(conn, artifact_id)
    if not heads:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")
    if len(heads) > 1:
        raise ScaffoldError(
            f"{artifact_id} 有 {len(heads)} 个分支 {[h['id'] for h in heads]}，"
            "没有「当前版本」。系统不替你挑一个（`§C10`）。"
        )
    return json.loads(heads[0]["content"])


def original_content_of(conn: sqlite3.Connection, artifact_id: str) -> dict:
    """第 1 个版本的内容。

    `§C2.2.1`：**原文本本身不可修改，必须原样保存**（「可追溯」依赖它）。
    所以凡是「指回用户当初提交的那段话」的派生视图，都该读这一版。

    取根版本（`parent_rev IS NULL`）而**不是** head：并发改出分支后 head 有两个，
    而 `revise` 明文拒绝替用户在两个分支里挑一个 —— 读视图就更不该挑。
    原文本只有一份，按定义取得到，不存在挑的问题。
    """
    row = conn.execute(
        "SELECT content FROM revision WHERE artifact_id = ? AND parent_rev IS NULL"
        " ORDER BY id LIMIT 1",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"{artifact_id} 一个版本都没有")
    return json.loads(row["content"])


def heads_of(conn: sqlite3.Connection, artifact_id: str) -> list[sqlite3.Row]:
    """没有子节点的版本 = 当前状态。多于一个 = 发生了并发修改，两条都留着。"""
    return conn.execute(
        "SELECT r.* FROM revision r"
        " WHERE r.artifact_id = ?"
        "   AND NOT EXISTS (SELECT 1 FROM revision c WHERE c.parent_rev = r.id)"
        " ORDER BY r.id",
        (artifact_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Relation（§C3.2：可追踪对象，不是外键字段）
# ---------------------------------------------------------------------------

def add_relation(
    conn: sqlite3.Connection, *,
    kind: str, from_id: str, to_id: str,
    origin: str, state: str = "active",
) -> int:
    """加一条结构边。**它有自己的身份、来源和状态。**

    `origin` 必填且区分人 / 机器 —— `§C2.4` 的「可计量」靠它：
    「AI 判定 vs 用户改判」的比例，分子分母都从这一列来。
    """
    if kind not in RELATION_KINDS:
        raise ScaffoldError(f"未知关系种类：{kind}")
    if state not in RELATION_STATES:
        raise ScaffoldError(f"未知 relation state：{state}")
    get(conn, from_id)
    get(conn, to_id)
    now = _now()
    cur = conn.execute(
        "INSERT INTO relation (kind, from_id, to_id, origin, state, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (kind, from_id, to_id, origin, state, now),
    )
    rid = int(cur.lastrowid)
    record_event(conn, "relation_added", origin, from_id,
                 {"relation": rid, "kind": kind, "to": to_id})
    conn.commit()
    return rid


def reject_relation(conn: sqlite3.Connection, relation_id: int, *, by: str) -> None:
    """用户推翻一条边（`§C2.4`「可拒绝」）。

    只改 relation 自己的状态，**不删** —— 删掉的话改判率就没法算了，
    而改判率是 `§C2.4` 明确要求计量的。
    """
    row = conn.execute(
        "SELECT * FROM relation WHERE id = ?", (relation_id,)
    ).fetchone()
    if row is None:
        raise ScaffoldError(f"不存在的 relation：{relation_id}")
    if row["state"] != "active":
        raise ScaffoldError(f"relation {relation_id} 已是 {row['state']}")
    conn.execute(
        "UPDATE relation SET state = 'rejected' WHERE id = ?", (relation_id,)
    )
    record_event(conn, "relation_rejected", by, row["from_id"],
                 {"relation": relation_id, "kind": row["kind"],
                  "origin": row["origin"]})
    conn.commit()


# ---------------------------------------------------------------------------
# Evidence 状态（#3：机器不得自授 verified）
# ---------------------------------------------------------------------------

def grant_verified(conn: sqlite3.Connection, artifact_id: str, *, hook: str) -> None:
    """**唯一**能把 status 写成 verified 的入口，且必须具名一个外部钩子。

    `§C9` #3 禁止 AI 生成内容直接成为 verified knowledge。
    这里把它落成一条机制：不是靠调用方自觉，是**没有别的写入路径**。
    """
    if not hook or not hook.strip():
        raise ScaffoldError("grant_verified 必须写明是哪个钩子授予的。")
    get(conn, artifact_id)
    conn.execute(
        "UPDATE artifact SET status = ? WHERE id = ?", (STATUS_HOOK_ONLY, artifact_id)
    )
    record_event(conn, "status_granted", f"hook:{hook}", artifact_id,
                 {"status": STATUS_HOOK_ONLY})
    conn.commit()


def set_status(conn: sqlite3.Connection, artifact_id: str, value: str, *, by: str) -> None:
    """非钩子路径。写 verified 一律拒绝。"""
    if value == STATUS_HOOK_ONLY:
        raise ScaffoldError(
            f"{by} 不能把 {artifact_id} 写成 verified —— "
            "verified 只能由外部钩子授予（§C9 #3）。走 grant_verified()。"
        )
    get(conn, artifact_id)
    conn.execute("UPDATE artifact SET status = ? WHERE id = ?", (value, artifact_id))
    conn.commit()


# ---------------------------------------------------------------------------
# 观测点（§C7.2）
# ---------------------------------------------------------------------------

def record_event(
    conn: sqlite3.Connection, kind: str, actor: str,
    subject_id: str | None, payload: dict | None = None,
) -> None:
    """记一条事实。**只记发生了什么，不记这说明了什么。**

    `§C7.2`：观测点应尽量记录既有事件，不新增需要用户配合的动作。
    因此本函数只被已有动作调用（重写 / 改判 / 放弃 / 确认），
    **没有任何一个 event kind 是为了采集而新造的用户动作。**
    """
    conn.execute(
        "INSERT INTO event (kind, actor, subject_id, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        (kind, actor, subject_id, json.dumps(payload or {}, ensure_ascii=False), _now()),
    )


def events_of_kind(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    """给上层派生视图用。**只读**。"""
    return conn.execute(
        "SELECT * FROM event WHERE kind = ? ORDER BY id", (kind,)
    ).fetchall()
