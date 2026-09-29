"""Arena 的命令行入口 —— **人用的那一面**。

    python cli.py new "<问题>" <谁>
    python cli.py submit <debate_id> <谁> "<你的原话>"
    python cli.py rewrite <debate_id> <draft_id> <谁> "<重写的那句话>"
    python cli.py confirm <debate_id> <draft_id> <谁>
    python cli.py view <debate_id>
    python cli.py chart <topic_id>
    python cli.py observe
    python cli.py upper <debate_id> [propose|promote <谁>|name <ctx_id> <谁> "<名字>"]

库文件默认 `arena.db`，用环境变量 `ARENA_DB` 换。
要一份干净的先 `rm arena.db`，没有「重置」命令 —— 删文件就是重置。

--- 这个文件是什么，不是什么 ---------------------------------------------

**薄的一层。** 它把库里已有的动作串成一条人走得通的路，
**不新增任何写路径**，也不自己判断任何事。

`§C5` 说的那些闸门（必须逐条、不许默认勾选、不许一键全部确认）
在这里**只被调用，不被绕过** —— 所以确认是**一条一条问**的，
没有「敲一串 key 一次交掉」那种写法。**这是故意的**：
`§C5` 明写「确认环节是产品价值第一次交付的地方，不是流程摩擦」，
所以这个文件里最要紧的不是少敲几下。

--- 还没接的东西（别把「能跑通」读成「产品完整」）------------------------

- **投票的「投」没接。** `cast_vote()` 要一份**渲染那一刻**抓的
  `vote_context()`（`§C12.3`）—— 那要求先有「投票页」这个东西，
  而「选项怎么呈现给人」是产品判断，不是实现细节。见 DECLARATION §13。
  `chart` 只接了**看**那一面（只读派生视图，不写库，`§C2.5` 第 3 档）。
- **Ctrl-C / 读到输入结束都不算「不提交」。** 那种情况下草稿留在 `open`，
  一个字都不写。理由：那是**用户离开了**还是**脚本出错了**，这里分不出来，
  分不出来就不许替它归因（`§C7.2` 的「中途放弃」要的是真放弃）。
  真要不提交，就在确认界面敲 `q` —— 那是明确的，会被记下来。
- **上层归纳默认可跑，但在真实库上休眠。** `upper` 的判据是三个**结构性量**
  （被质询 / 被修正 / 争议次数），现在的库太新，三个都是 0 ——
  那是 `§C7.1` 预言的结果，不是失败。要看到东西得先有重复质询。
- **上层节点没有「删除」入口**，也没有合并两个 `Context` 的动作。
  `§C10` 只进不退，合并要新增结构而不是抹掉旧的。
"""

from __future__ import annotations

import os
import sqlite3
import sys

from _console import force_utf8

import confirm
import debate
import observe
import scaffold
import upper
import vote


def _open() -> sqlite3.Connection:
    conn = scaffold.connect(os.environ.get("ARENA_DB", "arena.db"))
    scaffold.init(conn)
    confirm.ensure_schema(conn)
    return conn


def _ask(prompt: str) -> str | None:
    """读一行。**读不到就返回 None**，不装作读到了空串。

    空串是用户按了回车（`§C5.3`：空着比填错好 —— 那是一个回答），
    读不到是另一回事。这两个必须分得开，所以这里不 `or ""`。
    """
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return None


def _who(argv: list[str], i: int, usage: str) -> str:
    if len(argv) <= i:
        raise scaffold.ScaffoldError(f"缺参数。\n用法：{usage}")
    return argv[i]


# ---------------------------------------------------------------------------

def cmd_new(conn: sqlite3.Connection, argv: list[str]) -> int:
    usage = 'python cli.py new "<问题>" <谁>'
    if len(argv) <= 3:
        raise scaffold.ScaffoldError(f"缺参数。\n用法：{usage}")
    question, who = argv[2], _who(argv, 3, usage)
    out = debate.open_debate(conn, question=question, by=who)
    print(f"debate: {out['debate']}")
    print(f"  「{question}」（由 {who} 提出）")
    print()
    print("它**不带 Topic** —— 题目只是这个容器的标题。Topic 只能由一次")
    print("「提交 → 分割 → 逐条确认」产生，讨论里第一段原文也是这么进去的。")
    print()
    print(f"下一步：python cli.py submit {out['debate']} {who} \"<你的原话>\"")
    return 0


def cmd_submit(conn: sqlite3.Connection, argv: list[str]) -> int:
    usage = 'python cli.py submit <debate_id> <谁> "<你的原话>"'
    if len(argv) <= 4:
        raise scaffold.ScaffoldError(f"缺参数。\n用法：{usage}")
    debate_id, who = argv[2], argv[3]
    text = " ".join(argv[4:])
    draft_id = confirm.propose(conn, text=text, by=who)
    print(confirm.render(conn, draft_id))
    print()
    print("─" * 64)
    print(f"draft: {draft_id}")
    print(f"下一步：python cli.py confirm {debate_id} {draft_id} {who}")
    return 0


def cmd_rewrite(conn: sqlite3.Connection, argv: list[str]) -> int:
    usage = 'python cli.py rewrite <debate_id> <draft_id> <谁> "<重写的那句话>"'
    if len(argv) <= 5:
        raise scaffold.ScaffoldError(f"缺参数。\n用法：{usage}")
    debate_id, old, who = argv[2], argv[3], argv[4]
    text = " ".join(argv[5:])
    new_id = confirm.revise(conn, old, text=text, by=who)
    print(f"{old} → {new_id}（旧的一份一行没改，`§C10`）")
    print()
    print(confirm.render(conn, new_id))
    print()
    print("─" * 64)
    print(f"draft: {new_id}")
    print(f"下一步：python cli.py confirm {debate_id} {new_id} {who}")
    return 0


def cmd_confirm(conn: sqlite3.Connection, argv: list[str]) -> int:
    usage = "python cli.py confirm <debate_id> <draft_id> <谁>"
    if len(argv) <= 4:
        raise scaffold.ScaffoldError(f"缺参数。\n用法：{usage}")
    debate_id, draft_id, who = argv[2], argv[3], argv[4]

    print(confirm.render(conn, draft_id))
    p = confirm.payload_of(conn, draft_id)
    props = p["propositions"]

    print()
    print("=" * 64)
    print("§C5：**逐条**问，一条一条来。")
    print("  y = 这条意思没被歪曲    n = 意思被歪曲了    q = 不提交")
    print("  回车 = 不回答 —— **不回答不会被算成同意**，也写不进去。")
    print("=" * 64)

    answers: dict[str, str] = {}
    for x in props:
        got = _ask(f'  {x["key"]}. 「{x["text"]}」  意义未被歪曲？[y/n/q] ')
        if got is None:
            print("读到输入结束 —— **一个字都没写**（草稿还在，状态没变）。")
            return 2
        if got == "q":
            confirm.abandon(conn, draft_id, by=who)
            print(f"已记下「不提交」（`§C5.6` ③ A 类：已输入后离开）。{draft_id} → abandoned")
            return 0
        answers[x["key"]] = got

    distorted = [k for k, v in answers.items() if v == "n"]
    blank = [k for k, v in answers.items() if v not in ("y", "n")]

    if distorted:
        # `§C5.6` ②：**先点了「意义被歪曲」再重写**才计入换说法率。
        # 所以这个动作必须在这里发生，不能等到重写的时候补记 —— 补记就是编。
        confirm.mark_distorted(conn, draft_id, by=who,
                               note=f"确认界面指出：{distorted}")
        print()
        print(f"你指出了 {distorted} 意义被歪曲。按 `§C5.6` 只有两条路：")
        print(f"  ① 换说法重交：python cli.py rewrite {debate_id} {draft_id} {who} \"<重写的话>\"")
        print(f"  ② 不提交。")
        print(f"这份 draft 不会再被写进结构（状态 distorted）。")
        return 0

    if blank:
        print()
        print(f"这几条没有回答：{blank}")
        print("**没有写入任何东西。**挑子集等于替没答的那些做了决定（`§C2.2.2`、`§C5`）。")
        print(f"再走一次：python cli.py confirm {debate_id} {draft_id} {who}")
        return 2

    # ---- 缺口②：节点类型走**第 2 档**（默认生效 + 可推翻），所以问法是
    # 「默认 X，回车就用它」，不是「不选就写不进去」。这个默认**会被记进库里**
    # （`types_defaulted` 事件），可抽检、可改判 —— 无痕的默认才不是第 2 档。
    cands = {x["key"]: list(x["type_candidates"])
             for x in props if x.get("type_candidates")}
    choices: dict[str, str] = {}
    if cands:
        print()
        print("── 节点类型（`§C2.5` 第 2 档：默认生效、可推翻）")
        print("   回车 = 用默认。**这不是默认勾选** —— 默认那一条会记进库里，")
        print("   可抽检、可改判；要推翻就现在敲一个候选，或者事后走 resolve_types。")
        for k, c in cands.items():
            while True:
                more = f'，也可 {" 或 ".join(c[1:])}' if len(c) > 1 else ""
                got = _ask(f"   {k} 的节点类型：默认 {c[0]}{more} → ")
                if got is None:
                    print("读到输入结束 —— **一个字都没写**（草稿还在，状态没变）。")
                    return 2
                if got == "":
                    break
                if got in c:
                    choices[k] = got
                    break
                print(f"     {got!r} 不在候选 {c} 里。再敲一次，或回车用默认。")

    if choices:
        confirm.resolve_types(conn, draft_id, by=who, choices=choices)
    out = debate.open_topic(conn, debate_id=debate_id, draft_id=draft_id, by=who,
                            reviewed=[x["key"] for x in props])
    print()
    print(f"已写入。Topic {out['topic']}，命题 {len(out['claims'])} 条"
          f"（{'、'.join(f'{k}→{v}' for k, v in out['claims'].items())}）")
    print(f"下一步：python cli.py view {debate_id}")
    return 0


def cmd_view(conn: sqlite3.Connection, argv: list[str]) -> int:
    if len(argv) <= 2:
        raise scaffold.ScaffoldError("缺参数。\n用法：python cli.py view <debate_id>")
    print(debate.render(conn, argv[2]))
    return 0


def cmd_chart(conn: sqlite3.Connection, argv: list[str]) -> int:
    """票数的折线图（`§C12.2` 的「公众倾向」那一项）。

    按 **Topic** 画，不按 Debate —— 票挂在 Topic 下（`§C12.5`），
    一个 Debate 下的几个 Topic 各有各的投票上下文，合成一张图就是跨议题相加。

    颜色**不在这里判断**：`vote.chart()` 自己按 `NO_COLOR` / 是不是终端决定。
    `cli.py` 只是把它印出来 —— 这一层不做判断，判断在 `vote.py`。
    """
    if len(argv) <= 2:
        raise scaffold.ScaffoldError("缺参数。\n用法：python cli.py chart <topic_id>")
    print(vote.chart(conn, argv[2]))
    return 0


def cmd_observe(conn: sqlite3.Connection, argv: list[str]) -> int:
    print(observe.render(conn))
    return 0


def cmd_upper(conn: sqlite3.Connection, argv: list[str]) -> int:
    """上层上下文：看（默认）/ 提议 / 建 / 命名（`§C7.1`）。

        python cli.py upper <debate_id>                     # 只读，看现在有什么
        python cli.py upper <debate_id> propose             # 只读，看**候选**
        python cli.py upper <debate_id> promote <谁>        # 写：把候选建成节点
        python cli.py upper <debate_id> name <ctx_id> <谁> "<名字>"

    **默认是只读的**，这点和 `view` / `chart` / `observe` 一致 ——
    要写就得把动作词说出来。理由：提议是**高频**动作（每次重算都会跑），
    建是**低频**动作。默认写的话，跑几次就多出一堆节点，而
    「不得不清理自动产出的垃圾」正是让人开始乱删结构的起点。

    ⚠️ `promote` 建出来的节点**名字是空的**，必须由人来 `name` 一次。
    这不是「还没实现自动命名」，是**这条设计能免确认的全部理由** ——
    名字留空，系统就一句话都没说（`§C7.1` ③，见 `upper.py` 模块开头）。
    """
    if len(argv) <= 2:
        raise scaffold.ScaffoldError(
            "缺参数。\n用法：python cli.py upper <debate_id> [propose|promote <谁>"
            "|name <ctx_id> <谁> \"<名字>\"]")
    debate_id, action = argv[2], (argv[3] if len(argv) > 3 else "view")

    if action == "view":
        print(upper.render(conn, debate_id))
        return 0

    if action == "propose":
        out = upper.propose_clusters(conn)
        print(f"上层候选（规则集 {out['version']}）—— **只读**，一个字都没写")
        print()
        print("三个结构量（`§C7.1` 白名单，不含任何热度类信号）：")
        for k, counts in out["count_signals"].items():
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            shown = "、".join(f"{t}×{n}" for t, n in top) if top else "（空）"
            print(f"    {k:<18} {len(counts)} 个目标   {shown}")
        print()
        if not out["candidates"]:
            # 空结果**永远附一句话**（同 `hints.scan()` 的规矩）
            print("候选：0 条。")
            print()
            print("⚠️ 「还没有」和「算不出来」不是一回事：")
            for b in upper.BLIND_SPOTS:
                print(f"    · {b}")
            return 0
        print(f"候选：{len(out['candidates'])} 条")
        for c in out["candidates"]:
            print(f"    [{c['signal']}] 依据 {len(c['evidence_ids'])} 条："
                  f"{c['evidence_ids']}")
            print(f"         名字：{c['name']}（由人来给 —— 系统一个字都没说）")
        print()
        print(f"要建的话：python cli.py upper {debate_id} promote <谁>")
        return 0

    if action == "promote":
        if len(argv) <= 4:
            raise scaffold.ScaffoldError(
                "缺「谁」。\n用法：python cli.py upper <debate_id> promote <谁>")
        out = upper.propose_clusters(conn)
        if not out["candidates"]:
            print("没有候选可建 —— 底层的结构性信号还没攒够（见 upper.py 的 BLIND_SPOTS）。")
            return 0
        # 建之前把**将要发生的事**说清楚，不要静默写库
        print(f"将建立 {len(out['candidates'])} 个上层节点，名字**全部留空**。")
        print("这只影响「排序 / 默认展开 / 候选」，不改底层任何一个字。")
        made = upper.promote_candidates(
            conn, candidates=out["candidates"], by=argv[4], debate_id=debate_id)
        print()
        print(f"已建：{'、'.join(made)}")
        print("它们现在**没有名字** —— 起名之前，节点上只有它的依据，没有一句话。")
        print("下一步：给它们起名")
        for cid in made:
            print(f"    python cli.py upper {debate_id} name {cid} <谁> \"<名字>\"")
        return 0

    if action == "name":
        if len(argv) <= 6:
            raise scaffold.ScaffoldError(
                "缺参数。\n用法：python cli.py upper <debate_id> name <ctx_id> <谁> \"<名字>\"")
        rev = upper.rename_context(conn, context_id=argv[4], name=argv[6], by=argv[5])
        print(f"已改名：「{argv[6]}」")
        print(f"走 revise（不覆盖）—— 版本 {rev}。旧名留在版本表里。")
        return 0

    raise scaffold.ScaffoldError(
        f"不认识的动作 {action!r}。可选：view / propose / promote / name")


COMMANDS = {
    "new": cmd_new,
    "submit": cmd_submit,
    "rewrite": cmd_rewrite,
    "confirm": cmd_confirm,
    "view": cmd_view,
    "chart": cmd_chart,
    "observe": cmd_observe,
    "upper": cmd_upper,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 2
    conn = _open()
    try:
        return COMMANDS[argv[1]](conn, argv)
    except scaffold.ScaffoldError as e:
        # 库层拒绝的动作**照原话印出来**，不换成一句「操作失败」——
        # 那些话里写着为什么不许（`§C5` / `§C2.2.2` / `§C10`），
        # 换成通用错误信息等于把理由藏起来。
        print(f"做不到：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    force_utf8()
    raise SystemExit(main(sys.argv))
