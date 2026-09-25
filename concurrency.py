"""`§T2` 第 10 步：把两个**真正独立**的操作主体放到同一份状态上。

`§C11.2` 的判据表逐条落到本文件 —— 写下来是因为**这一节最容易被做成剧本**：

| 判据 | 本文件怎么做的 |
|---|---|
| ✅ 准备**独立操作主体** | **两个独立的操作系统进程**，各自 `sqlite3.connect()` 拿自己的连接 |
| ✅ 让它们**无协调**地操作同一份状态 | 进程间**没有任何**同步原语 —— 没有 barrier、没有队列、没有共享内存、没有约定的开始时刻 |
| ❌ 禁止直接改数据库 / 手工补状态 | worker **只调用产品 API**（`confirm` / `debate` / `vote`）。本文件没有一条 `INSERT` / `UPDATE`（B9 盯着） |
| ❌ 禁止注入延迟 / 写死竞争窗口 | **一处 `sleep` 都没有**（B9 盯着）。谁先谁后由操作系统调度决定，不由我决定 |
| ✅ 一轮没暴露就**如实说** | `report()` 里那一节：有现象就列，没有就写「**本轮未暴露**」 |

`§C11.3`：本阶段**不预先引入** Kafka / 消息队列 / Redis Cluster / 分布式锁。

**连 SQLite 自己的 WAL 我都没开。** 换日志模式也是一种机制，
而 `§C11.2` 的要求是「先让真实并发把问题打出来，再决定引入哪些机制」。
先按默认跑，看它打出什么。

    跑：python concurrency.py run [参与人数] [每人轮数]

---

**2026-09-25 改名为 `concurrency.py`**（原 `concurrent.py`）：原名字把标准库的
`concurrent` 包**遮住了**（`sys.path[0]` 是本目录），于是本目录下
`import asyncio` / `from unittest import mock` 会报
`ModuleNotFoundError: No module named 'concurrent.futures'` —— 报错里全是 `asyncio`，
和出错的地方毫无关系。理由、代价、以及「为什么不挪进 `samples/`」见 DECLARATION §15。

只改了**模块名**：`arena-concurrent.db` / `%TEMP%\arena-concurrent-*` 这些
**运行时产物名**没动 —— §8.2 记着那个路径，改了就把留档的现场说岔了。

---

**关于「怎么算暴露了问题」**：worker 把每一次动作的结果都记下来，
**包括异常**。异常不是失败 —— 异常正是本步要找的东西。
正常跑完、一条异常都没有，那就是「本轮未暴露」，照实写，不造。
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import confirm
import debate
import scaffold
import vote

ACTORS = ("甲", "乙", "丙", "丁")

# 两个人各自说的话。**故意让它们指向同一个议题下的同一批 Claim** ——
# 两个人在同一个话题下说话，本来就会撞在同一批对象上。
LINES = {
    "甲": ["远程办公省下通勤时间。", "在家更容易被家务打断。", "效率高低要看工作类型。"],
    "乙": ["远程协作工具已经够用了。", "缺少非正式沟通会丢信息。", "自律的人在哪都一样。"],
    "丙": ["管理层看不见过程会焦虑。", "办公室的随机碰撞有价值。"],
    "丁": ["通勤本身就是一种消耗。", "居家办公的电费谁出。"],
}


def worker(db_path: str, debate_id: str, actor: str, rounds: int, out_path: str) -> int:
    """一个独立进程里的一次参与。**只用产品 API，不做任何同步。**

    `debate_id` 是**由父进程传进来的**，不在库里自己找 —— 这样本文件里
    连一条 `SELECT` 都不需要（B9 盯着：并发装置里出现任何一条 SQL 都值得问一句
    「这是产品行为还是我在摆布状态」）。
    """
    log: list[dict] = []
    conn = scaffold.connect(db_path)
    scaffold.init(conn)
    confirm.ensure_schema(conn)

    def attempt(what: str, fn):
        """做一件事，**成功失败都记**。失败不中断 —— 这一轮要继续跑下去。"""
        try:
            got = fn()
            log.append({"actor": actor, "动作": what, "结果": "ok",
                        "返回": got if isinstance(got, (str, int, dict)) else str(got)})
            return got
        except Exception as e:                       # noqa: BLE001 —— 就是要抓全部
            log.append({
                "actor": actor, "动作": what, "结果": "异常",
                "异常类型": type(e).__name__,
                "异常消息": str(e)[:400],
                "最后一行": traceback.format_exc().strip().splitlines()[-1][:200],
            })
            return None

    try:
        topic = debate.view(conn, debate_id)["topics"][0]["topic"]["id"]
    except Exception as e:                           # noqa: BLE001
        # 连开场视图都读不出来，这一轮就没有可做的事了。记下来，不假装跑过。
        log.append({"actor": actor, "动作": "开场 view", "结果": "异常",
                    "异常类型": type(e).__name__, "异常消息": str(e)[:400]})
        Path(out_path).write_text(json.dumps(log, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        return 0

    for i in range(rounds):
        text = LINES[actor][i % len(LINES[actor])]

        # ① 走完整的语义确认链（`§C5`）—— **这是唯一的写路径**
        #
        # 2026-09-25 之前这里还有第二步「直接提交一个立场」（`debate.add_position`）：
        # 不分割、不逐条确认、只存 `{text, raw_text}`。那条路已经删了 ——
        # 一条内容现在只有这一条路，本装置压的也就是产品真跑的那条。
        draft = attempt("propose", lambda t=text: confirm.propose(conn, text=t, by=actor))
        claim = None
        if draft:
            payload = confirm.payload_of(conn, draft)
            keys = [p["key"] for p in payload["propositions"]]
            # 缺口② 的定类型这一步也走一遍。现在的 `LINES` 里一个因果连接词都没有，
            # 所以这一句其实不会触发 —— 但那说明的是**语料碰巧躲开了**，
            # 不是装置覆盖了它。留着，改 `LINES` 的人不必先读过缺口②。
            # 挑法固定取候选第一个：本装置的用途是压并发，不是判类型。
            cands = {p["key"]: p["type_candidates"]
                     for p in payload["propositions"] if p.get("type_candidates")}
            if cands:
                attempt("resolve_types", lambda d=draft, c=cands:
                        confirm.resolve_types(conn, d, by=actor,
                                              choices={k: v[0] for k, v in c.items()}))
            made = attempt("confirm", lambda d=draft, k=keys, t=topic:
                           confirm.confirm(conn, d, by=actor, reviewed=k, topic_id=t))
            if made:
                # ② 要改的是「自己刚提交的那一条」。一句话可能被切成多条，
                #    取第一条 —— 这里压的是并发，不是分割精度。
                claim = list(made["claims"].values())[0]

        # ② **改自己刚提交的那一条** —— 并发修改就发生在这里
        if claim:
            attempt("amend", lambda c=claim:
                    debate.amend_own_claim(conn, claim_id=c,
                                           text=text + "（我想说清楚点）", by=actor))

        # ③ 拿当前视图，挑一条**别人**的 Claim 下手 —— 两个进程会挑到同一批
        others = attempt("view_and_pick", lambda: _others(conn, debate_id, actor)) or []

        if others:
            target = others[i % len(others)]
            attempt("add_evidence", lambda x=target:
                    debate.add_evidence(conn, claim_id=x, text=f"{actor} 看到的材料。",
                                        by=actor, kind="supports"))
            attempt("challenge", lambda x=target, t=text:
                    debate.challenge(conn, target_id=x, text=f"{actor} 不同意：{t}", by=actor))

        # ④ 投票。`seen` 在**提交前那一刻**抓 —— 抓完到提交之间别人可能改过，
        #    那段缝就是本步要看的东西之一，不去抹平它。
        attempt("vote", lambda: vote.cast_vote(
            conn, topic_id=topic, choice=_first_claim(conn, debate_id),
            by=actor, seen=vote.vote_context(conn, topic),
            sampling="none-mvp", prior_results_visible=None,
            repeat_participation=None))

    conn.close()
    Path(out_path).write_text(json.dumps(log, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return 0


def _others(conn: sqlite3.Connection, debate_id: str, actor: str) -> list[str]:
    """视图里**别人的** Claim。**顺序就是视图给的顺序** —— 不排序、不打乱，
    两个进程看到同一份状态就会挑到同一条，那正是要撞的地方。"""
    return [c["claim"]["id"]
            for t in debate.view(conn, debate_id)["topics"] for c in t["claims"]
            if c["claim"]["origin"] != actor]


def _first_claim(conn: sqlite3.Connection, debate_id: str) -> str:
    for t in debate.view(conn, debate_id)["topics"]:
        if t["claims"]:
            return t["claims"][0]["claim"]["id"]
    raise scaffold.ScaffoldError("还没有 Claim 可投")


def _confirmed(
    conn: sqlite3.Connection, *, debate_id: str, text: str, by: str,
    topic_id: str | None = None,
) -> str:
    """提交一句话并确认进结构，返回它**第一条**命题的 id。**只用产品 API。**

    `topic_id=None` 新开一个 Topic；给一个已有 Topic 就挂上去。
    两条出口都落到 `confirm.confirm()` —— 和 `debate.add_to_topic` 的 docstring 同一条规矩。
    """
    draft = confirm.propose(conn, text=text, by=by)
    keys = [x["key"] for x in confirm.payload_of(conn, draft)["propositions"]]
    if topic_id is None:
        out = debate.open_topic(conn, debate_id=debate_id, draft_id=draft,
                                by=by, reviewed=keys)
    else:
        out = debate.add_to_topic(conn, topic_id=topic_id, draft_id=draft,
                                  by=by, reviewed=keys)
    return list(out["claims"].values())[0]


def setup(db_path: str) -> dict:
    """初始状态。**这不是协调** —— 这是「一份要被并发操作的状态」本身。"""
    conn = scaffold.connect(db_path)
    scaffold.init(conn)
    confirm.ensure_schema(conn)
    d = debate.open_debate(conn, question="远程办公效率更高吗？", by="发起人")
    # 两份初始立场也走真路（分割 → 逐条确认 → 进讨论），第二份挂进第一个 Topic。
    # **装置里不许有第二条写路径** —— 否则它压的是产品不走的那条。
    first = _confirmed(conn, debate_id=d["debate"], text="远程办公效率更高。", by="甲")
    _confirmed(conn, debate_id=d["debate"], text="坐办公室效率更高。", by="乙",
               topic_id=first)
    conn.close()
    return d


def run(workers: int = 2, rounds: int = 3, db_path: str | None = None) -> dict:
    """**同时**起 N 个进程。没有开始信号 —— 谁先跑到由操作系统决定。"""
    tmp = tempfile.mkdtemp(prefix="arena-concurrent-")
    db = db_path or os.path.join(tmp, "arena.db")
    d = setup(db)
    debate_id = d["debate"]

    procs = []
    for i in range(workers):
        actor = ACTORS[i % len(ACTORS)]
        out = os.path.join(tmp, f"log-{i}.json")
        procs.append((
            actor, out,
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__),
                 "worker", db, debate_id, actor, str(rounds), out],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ),
        ))

    logs, crashes = [], []
    for actor, out, p in procs:
        _, err = p.communicate()
        if os.path.exists(out):
            logs.extend(json.loads(Path(out).read_text(encoding="utf-8")))
        if p.returncode != 0:
            crashes.append({"actor": actor, "code": p.returncode,
                            "stderr": err.decode("utf-8", "replace")[-800:]})

    return {"db": db, "logs": logs, "crashes": crashes,
            "workers": workers, "rounds": rounds,
            "异常": [x for x in logs if x["结果"] == "异常"],
            "交错": _interleaving(db, [p[0] for p in procs])}


def _interleaving(db: str, actors: list[str]) -> dict:
    """两个主体**到底有没有真的同时在跑**。

    这一项不测就报「未暴露」，是一句**没有分母的结论**：
    两个进程先后跑完，同样一条异常都不会有，而那样的「未暴露」什么都说明不了。
    跟 `observe._ratio(0, 0)` 显示「算不出」是同一条规矩 ——
    **没测到的东西，不许长得像测到了零。**

    怎么测：`event` 表按 id 排（那是产品本来就在记的先后），数 actor 的换手。
    整个连成一条长段 = 它们其实是排队跑的。
    **这不是注入延迟测出来的** —— 就是数已经记下来的事实（`§C11.2`）。
    """
    conn = scaffold.connect(db)
    rows = conn.execute(
        "SELECT actor FROM event WHERE actor IN (%s) ORDER BY id"
        % ",".join("?" * len(actors)), actors,
    ).fetchall()
    conn.close()
    seq = [r["actor"] for r in rows]
    runs = [a for a, _ in itertools.groupby(seq)]
    longest = max((len(list(g)) for _, g in itertools.groupby(seq)), default=0)
    return {"事件数": len(seq), "换手次数": max(len(runs) - 1, 0),
            "最长连续段": longest, "轮廓": "".join(runs)}


def report(result: dict) -> str:
    """`§T2` 第 11 步要的三段：**现象 / 复现步骤 / 根因**。

    根因这一段**只有在真有现象时才填** —— 没现象就是「本轮未暴露」。
    """
    logs, 异常 = result["logs"], result["异常"]
    out = ["=" * 68, "§T2 第 10/11 步：两个独立进程、无协调、同一份状态", "=" * 68, ""]
    out.append(f"动作 {len(logs)} 次，其中异常 {len(异常)} 次。")

    by_actor: dict[str, int] = {}
    for x in logs:
        by_actor[x["actor"]] = by_actor.get(x["actor"], 0) + 1
    out.append("各主体动作数：" + "，".join(f"{k} {v}" for k, v in by_actor.items()))

    # ⚠️ 先报交替，再报异常。反过来的话，一串「0 异常」会被当成结论读走，
    # 而它未必有分母 —— 两个进程先后跑完也是 0 异常。
    ic = result["交错"]
    out.append(f"事件 {ic['事件数']} 条，换手 {ic['换手次数']} 次，"
               f"最长连续段 {ic['最长连续段']} 条")
    out.append(f"交错轮廓：{ic['轮廓']}")
    if ic["换手次数"] == 0:
        out.append("  ⚠️ **换手 0 次 = 两个主体是先后跑的，不是同时跑的。**")
        out.append("  这一轮的「未暴露」没有分母，不构成任何证据。")
    elif ic["最长连续段"] >= ic["事件数"] * 3 // 4:
        out.append(f"  ⚠️ 最长连续段占了 {ic['最长连续段']}/{ic['事件数']} —— "
                   "大部分时间是排队跑的，「未暴露」的说服力有限。")
    out.append("")

    if result["crashes"]:
        out.append("【现象】进程级崩溃（连日志都没写完）")
        for c in result["crashes"]:
            out.append(f"  {c['actor']} 退出码 {c['code']}：{c['stderr'].strip()}")
        out.append("")

    if not 异常:
        out += [
            "【现象】本轮**未暴露**问题。",
            "",
            "  这不是通过，也不是失败 —— 它是本轮的结果，照实记下来。",
            "  `§C11.2`：一轮跑完没暴露问题，如实汇报「本轮未暴露」，不得伪造。",
            "",
            "  注意这只说明**这一次跑法**没撞上，不说明系统并发安全。",
            "  要下结论需要更多轮次、更多主体、更长的交互 —— 那也是真实条件，不是剧本。",
        ]
        return "\n".join(out)

    kinds: dict[str, int] = {}
    for x in 异常:
        kinds[x["异常类型"]] = kinds.get(x["异常类型"], 0) + 1
    out.append("【现象】")
    for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        out.append(f"  {k}  ×{n}")
    out.append("")
    out.append("  逐条：")
    seen = set()
    for x in 异常:
        sig = (x["异常类型"], x["异常消息"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(f"    [{x['actor']}] {x['动作']} → {x['异常类型']}: {x['异常消息']}")
    out.append("")
    out.append("【复现步骤】")
    out.append(f"  python concurrency.py run {result['workers']} {result['rounds']}"
               f"      # {result['workers']} 个进程 × {result['rounds']} 轮")
    out.append(f"  跑到的库：{result['db']}")
    out.append("")
    out.append("【根因】")
    out.append("  **尚未定位。** 第 11 步要求「现象 / 复现步骤 / 根因」三者齐备才算完成，")
    out.append("  所以现在还**不算完成** —— 只完成了前两段。根因要读过代码再写，")
    out.append("  而 `§C11.2` 也要求先看清问题，**第 12 步才引入机制**。")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "worker":
        _, _, db, debate_id, actor, rounds, out = argv
        return worker(db, debate_id, actor, int(rounds), out)
    if len(argv) >= 2 and argv[1] == "setup":
        db = argv[2] if len(argv) > 2 else "arena-concurrent.db"
        setup(db)
        print(f"状态已建：{db}（这不是协调，这是要被并发操作的那份状态本身）")
        return 0
    if len(argv) >= 2 and argv[1] == "run":
        workers = int(argv[2]) if len(argv) > 2 else 2
        rounds = int(argv[3]) if len(argv) > 3 else 3
        print(report(run(workers, rounds)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
