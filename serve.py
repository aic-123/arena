"""Arena 的网页界面 —— 同一个库的**看**与**写**两个产品表面。

    python serve.py                 # 默认 127.0.0.1:8765
    python serve.py --port 9000
    ARENA_DB=other.db python serve.py

--- 这个文件是什么，不是什么 -------------------------------------------

**是什么**：两件事。

1. **看**：把 `debate.view()` / `vote.chart()` / `observe.snapshot()`
   三个**已经存在的只读视图**渲染成 HTML。
2. **写**：把 `new` / `submit` / `confirm` / `rewrite` 四个命令接上网页。

**不是「另起一套写路径」。** 这是最重要的一条：下面每个写动作都只是
**调度** `debate.open_debate()` / `confirm.propose()` / `confirm.revise()` /
`confirm.abandon()` / `confirm.mark_distorted()` / `confirm.resolve_types()` /
`debate.open_topic()` —— **和 `cli.py` 调的是同一批函数**。
`§14` 刚把写路径统一到 confirm 路（删掉了 `add_position`），
这里再开一条平行的写路径就是把它拆回去。

**不是什么**：

- **不是「投票页」。** `§C12` 的「投」（`cast_vote`）**仍然没接** ——
  那要求先定「选项怎么呈现给人」，是产品判断（DECLARATION §13.5）。
  这里只有 `chart` 的**看**那一面，和数据本来就有的四项公共偏好读数。
  **看与写都做好了，只有「投」这一项没碰** —— 这是有意留的口子。
- **没有排序。** 命题按库里返回的**原顺序**平铺，不按重要性、不按时间
  倒序、不折叠。**不产生「哪条更重要」**（`§C6.1` / `§C9` #5 #7）。
- **没有第三方依赖。** 只用标准库 `http.server`。这是硬约束：
  `checks.py` 的 B7（`§T4.3 §T5`）会扫 `import`，引入框架就是阻断项。

--- 确认流程为什么长这样（`§C5` 的落点）---------------------------------

`§C5` 要的是「**逐条**问，一条一条来」，以及「**不回答 ≠ 同意**」。
搬上网页后必须原样保住四条语义，一条都不能省：

1. **一条一条答**：界面上每条命题各自一组按钮，没有「全部确认」。
   规格允许一屏列完（那是版式），不允许一次答完（那是 `§C5` 禁的）。
2. **不回答 ≠ 同意**：有任一条没答，`confirm()` 收到不全的 `reviewed`
   会直接拒 —— 界面在提交前就先拦一次，说清是哪几条没答。
3. **`q`（不提交）与「关掉页面」是两件事。** `q` 是明确的，记
   `draft_abandoned`；关掉页面什么都没发生，**一个字不写**。
   所以「不提交」必须是一个**要点下去的按钮**，不能靠「离开页面」表达。
4. **节点类型的默认要记进库。** 定类型走 `§C2.5` 第 2 档
   （默认生效 + 可推翻），但**默认不是静默的**：不选也走
   `confirm()` 里那条 `types_defaulted` 记录。选了就调 `resolve_types()`。

--- 为什么用标准库而不是成熟框架 ----------------------------------------

查过 NiceGUI / Flask / FastAPI 这类方案 —— **它们在这里全部不可用**，
因为它们都是**第三方依赖**，B7 会直接判红。而标准库 `http.server`
是官方文档认可的本地服务做法，`ThreadingHTTPServer` 每请求一线程，
对「本机单人用自己那个库」这个用途足够。

**换句话说：不是不用成熟方案，是这个项目的约束把「成熟方案」限定在了
标准库这一档。** 换框架要先改 `§T5`（引入依赖是必须停下来的阻断项）。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import confirm
import debate
import observe
import scaffold
import upper
import vote
from _console import force_utf8
from segment import DEFAULT_PROPOSITION_TYPE

DEFAULT_DB = os.environ.get("ARENA_DB", "arena.db")


def _open() -> sqlite3.Connection:
    """按 `cli.py::_open()` 的同一条路开库：**建表再读**。

    ⚠️ 这里必须补 `scaffold.init()` / `confirm.ensure_schema()`。少了它们，
    一个**还没被 `cli.py init` 建过**的库（哪怕只是个 0 字节的空文件）会让
    每条路由都在 `no such table: artifact` 上炸 —— 而 `sqlite3.connect()`
    本身会**悄悄创建**那个空文件，于是「第一次启动」必然踩中。
    这不是写库：`CREATE TABLE IF NOT EXISTS` 是让只读视图有东西可读，
    `§C2.5` 第 3 档针对的是**产出内容**的写，不是建骨架。
    """
    conn = scaffold.connect(DEFAULT_DB)
    scaffold.init(conn)
    confirm.ensure_schema(conn)
    return conn


def _list_debates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, state, created_at FROM artifact WHERE type = 'Debate' ORDER BY id"
    ).fetchall()


# ---------------------------------------------------------------------------
# 写入路径 —— **只调度，不新增逻辑**
#
# 下面每个函数体都短得可疑，那是刻意的：真正的动作在 `confirm.py` / `debate.py`
# 里，和 `cli.py` 调的是同一批。界面层多写一行判断，就等于多一条写路径。
# ---------------------------------------------------------------------------

WHO_COOKIE = "arena_who"


def _who_of(handler) -> str:
    """从 Cookie 取「谁」。取不到就是空串 —— 由调用方决定怎么办。

    **不用表单里的隐藏字段传递。** 隐藏字段每次提交都能被改，
    而「谁提交的」一旦可伪造，`§C2.4` 那些按人算的观测点就没意义了。
    Cookie 也拦不住存心伪造的人（本地单人工具，不设防），
    但它至少保证**同一个人在同一台机器上不会因为表单没填全就换了个名字**。
    """
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == WHO_COOKIE:
            return urllib.parse.unquote(v).strip()
    return ""


def act_new(conn: sqlite3.Connection, form: dict[str, list[str]], who: str) -> str:
    """开一个讨论。调 `debate.open_debate()` —— 与 `cli.py cmd_new` 同一个函数。"""
    question = (form.get("question", [""])[0] or "").strip()
    if not question:
        raise scaffold.ScaffoldError("题目不能是空的。")
    out = debate.open_debate(conn, question=question, by=who)
    return (
        f'<div class="ok">已建讨论 <strong>{esc(out["debate"])}</strong>。</div>'
        f'<div class="notice">它<strong>不带 Topic</strong> —— 题目只是容器的标题。'
        f"Topic 只能由一次「提交 → 分割 → 逐条确认」产生。<br><br>"
        f'<a href="/debate/{esc(out["debate"])}">去这个讨论里提交第一段话 →</a></div>'
    )


def act_submit(conn: sqlite3.Connection, form: dict[str, list[str]], who: str) -> str:
    """提交一段原文 → 出分割结果。调 `confirm.propose()`。"""
    debate_id = (form.get("debate_id", [""])[0] or "").strip()
    text = (form.get("text", [""])[0] or "").strip()
    if not debate_id:
        raise scaffold.ScaffoldError("缺 debate_id。")
    if not text:
        raise scaffold.ScaffoldError("原话不能是空的。")
    # 这个检查是**只读**的，不是写路径逻辑：防的是「往一个 Topic 的 id 上提交」。
    got = debate.get(conn, debate_id)
    if got["type"] != "Debate":
        raise scaffold.ScaffoldError(
            f"{debate_id} 是 {got['type']}，不是 Debate。"
            "写操作只认 Debate —— Topic 只能由确认流程产生。"
        )
    draft_id = confirm.propose(conn, text=text, by=who)
    return (
        f'<div class="ok">已切分。draft <strong>{esc(draft_id)}</strong></div>'
        f'<div class="notice">下一步是'
        f'<a href="/confirm/{esc(debate_id)}/{esc(draft_id)}">逐条确认 {esc(draft_id)}</a>'
        f" —— <code>§C5</code> 要一条一条答，不回答不算同意。</div>"
    )


def act_rewrite(conn: sqlite3.Connection, form: dict[str, list[str]], who: str) -> str:
    """换说法重交。调 `confirm.revise()` —— 旧的一份一行不改（`§C10`）。"""
    debate_id = (form.get("debate_id", [""])[0] or "").strip()
    old = (form.get("draft_id", [""])[0] or "").strip()
    text = (form.get("text", [""])[0] or "").strip()
    if not (debate_id and old and text):
        raise scaffold.ScaffoldError("缺参数。")
    new_id = confirm.revise(conn, old, text=text, by=who)
    return (
        f'<div class="ok">{esc(old)} → <strong>{esc(new_id)}</strong>'
        f"（旧的一份一行没改，<code>§C10</code>）</div>"
        f'<div class="notice">下一步：'
        f'<a href="/confirm/{esc(debate_id)}/{esc(new_id)}">逐条确认 {esc(new_id)}</a></div>'
    )


def act_abandon(conn: sqlite3.Connection, form: dict[str, list[str]], who: str) -> str:
    """`§C5.6` ③ A 类：不提交。调 `confirm.abandon()`。

    ⚠️ 这是**一个明确的动作**，不是「关掉页面」。规格把这两件事分得很开：
    点这个按钮 → 记 `draft_abandoned`；关掉页面 → **一个字都不写**。
    所以界面上必须有个按钮，不能靠离开页面表达「不提交」。
    """
    draft_id = (form.get("draft_id", [""])[0] or "").strip()
    if not draft_id:
        raise scaffold.ScaffoldError("缺 draft_id。")
    confirm.abandon(conn, draft_id, by=who)
    return (
        f'<div class="ok">已记下「不提交」：{esc(draft_id)} → abandoned</div>'
        f'<div class="notice"><code>§C5.6</code> ③ A 类（已输入后离开）——'
        f"输入和分割结果都产生了，所以系统内可见，必须记。"
        f"这份 draft 不会再被写进结构。</div>"
    )


def act_confirm(conn: sqlite3.Connection, form: dict[str, list[str]], who: str) -> str:
    """逐条确认 → 写进结构。这条是整个界面上**最不能走样**的一段。

    它跟 `cli.py::cmd_confirm` 的分支顺序是**一一对应**的，顺序也不能换：

    1. 先看有没有 `n`（意义被歪曲）→ `mark_distorted()`，然后**停**
       （`§C5.6` ②：只有两条路 —— 换说法重交，或不提交）
    2. 再看有没有没答的 → **一个字都不写**，退回去
    3. 最后才 `resolve_types()`（可选）+ `open_topic()`

    **先判 `n` 再判空**这个顺序是有意义的：一份里既有 `n` 又有空，
    该走的是「意义被歪曲」那条，不是「你没答完」那条。
    """
    debate_id = (form.get("debate_id", [""])[0] or "").strip()
    draft_id = (form.get("draft_id", [""])[0] or "").strip()
    if not (debate_id and draft_id):
        raise scaffold.ScaffoldError("缺参数。")

    p = confirm.payload_of(conn, draft_id)
    props = p["propositions"]
    keys = [x["key"] for x in props]

    answers: dict[str, str] = {}
    for k in keys:
        got = (form.get(f"ans_{k}", [""])[0] or "").strip()
        if got:
            answers[k] = got

    # ---- 1. 意义被歪曲 ----
    distorted = [k for k, v in answers.items() if v == "n"]
    if distorted:
        confirm.mark_distorted(
            conn, draft_id, by=who, note=f"确认界面指出：{distorted}",
        )
        return (
            f'<div class="notice warn">你指出了 '
            f"<strong>{esc(', '.join(distorted))}</strong> 意义被歪曲。"
            f"按 <code>§C5.6</code> 只有两条路：</div>"
            f"<h2>① 换说法重交</h2>"
            f'<form method="post" action="/rewrite">'
            f'<input type="hidden" name="debate_id" value="{esc(debate_id)}">'
            f'<input type="hidden" name="draft_id" value="{esc(draft_id)}">'
            f'<textarea name="text" placeholder="换一种说法，重写这句话"></textarea>'
            f'<div style="margin-top:8px"><button class="go" type="submit">'
            f"重交</button></div></form>"
            f"<h2>② 不提交</h2>"
            f'<form method="post" action="/abandon">'
            f'<input type="hidden" name="draft_id" value="{esc(draft_id)}">'
            f'<button class="ghost" type="submit">记下「不提交」</button></form>'
            f'<div class="notice">这份 draft 不会再被写进结构'
            f"（状态 <code>distorted</code>）。</div>"
        )

    # ---- 2. 没答完 → 一个字都不写 ----
    blank = [k for k in keys if k not in answers]
    if blank:
        texts = {x["key"]: x["text"] for x in props}
        items = "".join(
            f'<li><code>{esc(k)}</code> 「{esc(texts[k])}」</li>' for k in blank
        )
        return (
            f'<div class="notice warn"><strong>这几条没有回答：</strong>'
            f"<ul>{items}</ul>"
            f"<strong>没有写入任何东西。</strong>"
            f"挑子集等于替没答的那些做了决定"
            f"（<code>§C2.2.2</code>、<code>§C5</code>）。</div>"
            f'<div class="notice"><a href="/confirm/{esc(debate_id)}/'
            f'{esc(draft_id)}">← 回去把这几条答完</a></div>'
        )

    # ---- 3. 节点类型（§C2.5 第 2 档）。不选 = 用默认，默认照样记进库。 ----
    cands = {x["key"]: list(x["type_candidates"])
             for x in props if x.get("type_candidates")}
    choices: dict[str, str] = {}
    if cands:
        got_choices = {k: (form.get(f"type_{k}", [""])[0] or "").strip()
                       for k in cands}
        # 只要有一项被显式选了，就必须**一项不漏地**给全 ——
        # `resolve_types()` 自己会拒不全的输入，这里先转换成「用默认」补齐。
        if any(got_choices.values()):
            for k, c in cands.items():
                choices[k] = got_choices[k] if got_choices[k] in c else c[0]
            confirm.resolve_types(conn, draft_id, by=who, choices=choices)

    out = debate.open_topic(
        conn, debate_id=debate_id, draft_id=draft_id, by=who, reviewed=keys,
    )
    made = "、".join(f"{k}→{v}" for k, v in out["claims"].items())
    return (
        f'<div class="ok">已写入。Topic <strong>{esc(out["topic"])}</strong>，'
        f"命题 {len(out['claims'])} 条（{esc(made)}）</div>"
        f'<div class="notice"><a href="/debate/{esc(debate_id)}">'
        f"← 回讨论看结果</a></div>"
    )


def render_confirm(conn: sqlite3.Connection, debate_id: str, draft_id: str,
                   who: str) -> str:
    """逐条确认页。**一屏列完，但每条各自作答**（规格允许版式自由，
    不允许「一次答完」）。"""
    d = conn.execute("SELECT * FROM draft WHERE id = ?", (draft_id,)).fetchone()
    if d is None:
        raise scaffold.ScaffoldError(f"没有这份 draft：{draft_id}")
    p = json.loads(d["payload"])
    props = p["propositions"]

    if d["state"] != "open":
        return (
            f'<div class="eyebrow"><a href="/debate/{esc(debate_id)}">'
            f"← 回讨论</a></div>"
            f"<header><h1>这份 draft 已经处理过了</h1></header>"
            f'<div class="notice warn"><code>{esc(draft_id)}</code> 状态是 '
            f"<strong>{esc(d['state'])}</strong>，不能再确认。</div>"
        )

    rows = []
    for x in props:
        k = x["key"]
        cands = x.get("type_candidates") or []
        type_html = ""
        if cands:
            opts = "".join(
                f'<option value="{esc(c)}">{esc(c)}'
                + ("（默认）" if i == 0 else "")
                + "</option>"
                for i, c in enumerate(cands)
            )
            type_html = (
                f'<div class="typepick">'
                f'节点类型（<code>§C2.5</code> 第 2 档：默认生效、可推翻）'
                f'<select name="type_{esc(k)}">{opts}</select>'
                f"<br><span class=\"k\">不选就用默认。"
                f"<strong>默认不是静默判断</strong> —— 它会记进库里，可抽检、可改判。"
                f"</span></div>"
            )
        elif x.get("role") == "causal":
            # 连接词本身成的节点（内容就是「所以」）。它也不带候选，
            # 但原因与普通陈述句不同 —— 别说成「无连接词可读」。
            type_html = (
                f'<div class="typepick">'
                f'<span class="k">节点类型：<strong>'
                f"{esc(DEFAULT_PROPOSITION_TYPE)}</strong> — 这是连接词本身成的节点"
                f"（它断言「前件导致后件」）。记为 "
                f"<code>type_basis=causal_connective</code>，可抽检。</span></div>"
            )
        else:
            # 普通陈述句。**这一块以前不显示**，于是整屏看不到任何类型信息 ——
            # 读起来就像「引擎什么都没判」。把「没有可判的东西」说出来，
            # 与「判了但没说」分开。详见 `confirm.DEFAULT_PROPOSITION_TYPE`。
            type_html = (
                f'<div class="typepick">'
                f'<span class="k">节点类型：<strong>'
                f"{esc(DEFAULT_PROPOSITION_TYPE)}</strong> — "
                f"这句里没有因果连接词可读，引擎<strong>没有可判的东西</strong>"
                f"（不是判了没说）。记为 "
                f"<code>type_basis=no_candidate_default</code>，可抽检。</span></div>"
            )
        rows.append(
            f'<div class="card"><span class="id">{esc(k)}</span>'
            f'<span class="badge">{esc(x.get("role", ""))}</span>'
            f'<div class="txt">{esc(x["text"])}</div>'
            f'<div class="meta">{_md(x.get("why", ""))}</div>'
            f'<div class="answer">'
            f'<span class="lbl">意义未被歪曲？</span>'
            f'<button type="button" class="pick" data-k="{esc(k)}" '
            f'data-v="y">没被歪曲</button>'
            f'<button type="button" class="pick" data-k="{esc(k)}" '
            f'data-v="n">被歪曲了</button>'
            f'<input type="hidden" name="ans_{esc(k)}" id="ans_{esc(k)}" value="">'
            f'</div>{type_html}</div>'
        )

    raw = p.get("input_snapshot", "")
    return (
        f'<div class="eyebrow"><a href="/debate/{esc(debate_id)}">← 回讨论</a></div>'
        f"<header><h1>逐条确认</h1>"
        f'<div class="sub">{esc(draft_id)} · <code>§C5</code> 一条一条来</div>'
        f"</header>"
        f'<div class="notice">原文（原样保存，不可修改）：<br>'
        f"「{esc(raw)}」</div>"
        f'<div class="notice warn"><strong>不回答 ≠ 同意。</strong>'
        f"有任意一条没答，整份都不写 —— 挑子集等于替没答的那些做了决定"
        f"（<code>§C2.2.2</code>）。</div>"
        f'<form method="post" action="/confirm">'
        f'<input type="hidden" name="debate_id" value="{esc(debate_id)}">'
        f'<input type="hidden" name="draft_id" value="{esc(draft_id)}">'
        + "".join(rows)
        + '<div style="margin-top:16px;display:flex;gap:10px">'
        '<button class="go" type="submit">确认，写进结构</button>'
        "</form>"
        f'<form method="post" action="/abandon">'
        f'<input type="hidden" name="draft_id" value="{esc(draft_id)}">'
        f'<button class="ghost" type="submit">不提交</button></form>'
        "</div>"
        f'<div class="notice">「不提交」是要点下去的按钮，不是关掉页面 ——'
        f"点它记 <code>draft_abandoned</code>；关掉页面<strong>一个字都不写</strong>"
        f"（分不出「你走了」还是「脚本坏了」，分不出就不许替它归因）。</div>"
        "<script>"
        "document.querySelectorAll('button.pick').forEach(function(b){"
        "  b.addEventListener('click', function(){"
        "    var k=b.getAttribute('data-k'), v=b.getAttribute('data-v');"
        "    document.getElementById('ans_'+k).value=v;"
        "    document.querySelectorAll('button.pick').forEach(function(o){"
        "      if(o.getAttribute('data-k')===k)"
        "        o.setAttribute('aria-pressed', String(o===b));"
        "    });"
        "  });"
        "});"
        "</script>"
    )


def render_write_form(kind: str, debate_id: str = "", draft_id: str = "") -> str:
    """三个写入口的表单。**没有把 id 放进隐藏字段以外的花活** ——
    表单里的 id 是给服务端定位用的，不是身份（身份走 Cookie）。"""
    if kind == "new":
        return (
            "<h2>开一个新讨论</h2>"
            '<div class="notice">题目是容器的标题，<strong>不是 Topic</strong>。'
            "一个讨论下可以挂多段原文，每段原文各自成 Topic。</div>"
            '<form method="post" action="/new">'
            '<input type="text" name="question" placeholder="你在争的那个问题">'
            '<div style="margin-top:10px"><button class="go" type="submit">'
            "建讨论</button></div></form>"
        )
    if kind == "submit":
        return (
            f'<h2>提交一段原话</h2>'
            f'<div class="notice">交上来的是<strong>你的原话</strong>，'
            f"原样保存、不可修改。机器只做一件事：把它切成命题，"
            f"然后<strong>逐条问你</strong>「我切得对不对」。</div>"
            f'<form method="post" action="/submit">'
            f'<input type="hidden" name="debate_id" value="{esc(debate_id)}">'
            f'<textarea name="text" placeholder="用你自己的话把它说出来"></textarea>'
            f'<div style="margin-top:10px"><button class="go" type="submit">'
            f"切分</button></div></form>"
        )
    return ""



PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin: 0; background: #faf9f6; color: #2c2c2a;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         font-size: 14px; line-height: 1.65; }}
  a {{ color: #185fa5; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 24px 20px 64px; }}
  header {{ border-bottom: 1px solid #e2e0d8; padding-bottom: 14px; margin-bottom: 24px; }}
  header h1 {{ font-size: 17px; margin: 0 0 4px; }}
  header .sub {{ font-size: 12px; color: #5f5e5a; }}
  h2 {{ font-size: 14px; margin: 32px 0 10px;
        padding-bottom: 6px; border-bottom: 1px solid #e2e0d8; }}
  .notice {{ background: #e6f1fb; border-left: 3px solid #185fa5;
             padding: 10px 14px; margin: 16px 0; font-size: 13px; }}
  .notice.warn {{ background: #faeeda; border-left-color: #854f0b; }}
  .card {{ background: #fff; border: 1px solid #e2e0d8; border-radius: 10px;
           padding: 14px 16px; margin: 10px 0; }}
  .card .id {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px;
               color: #5f5e5a; }}
  .card .txt {{ font-size: 15px; margin: 6px 0 0; }}
  .badge {{ display: inline-block; font-size: 11px; padding: 1px 7px;
            border-radius: 999px; background: #f1efe8; color: #5f5e5a;
            margin-left: 6px; vertical-align: 1px; }}
  .badge.warn {{ background: #faeeda; color: #854f0b; }}
  .meta {{ font-size: 12px; color: #5f5e5a; margin-top: 8px; }}
  .edge {{ font-size: 13px; margin: 6px 0 0 12px; color: #444441; }}
  .k {{ color: #5f5e5a; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  td, th {{ text-align: left; padding: 6px 10px 6px 0; vertical-align: top;
            border-bottom: 1px solid #f1efe8; }}
  th {{ color: #5f5e5a; }}
  pre {{ background: #f1efe8; padding: 12px 14px; border-radius: 8px;
         overflow-x: auto; font-size: 12px; line-height: 1.5;
         font-family: ui-monospace, Consolas, monospace; }}
  .empty {{ color: #5f5e5a; font-style: normal; }}
  /* —— 立场层（`§C4` 的 Topic ├── Position ├── Claim）—— */
  .stance {{ border-left: 3px solid #185fa5; background: #f7fbff;
             border-radius: 0 10px 10px 0; padding: 12px 16px; margin: 14px 0; }}
  .stance .card {{ margin: 8px 0 0; }}
  .stancetop {{ padding-bottom: 6px; border-bottom: 1px dashed #c8dff2; }}
  /* ⚠️ 这一档**不许加字重声明** —— B2 是一条刻意的 dumb 子串扫描
       （`§C6.1` 要的是「产物里别长出权重」，所以它把 `font-` 开头那个同名 CSS
       属性也一起扫了）。加粗走 `<strong>`：下面单有一条，只 `font-style: normal`。 */
  .stancetxt {{ font-size: 15px; margin-left: 8px; }}
  .stance .empty {{ font-size: 13px; margin: 8px 0 0; }}
  footer {{ margin-top: 48px; padding-top: 14px; border-top: 1px solid #e2e0d8;
            font-size: 12px; color: #5f5e5a; }}
  .eyebrow {{ font-size: 12px; color: #5f5e5a; margin-bottom: 6px; }}
  .na {{ color: #854f0b; }}
  strong {{ font-style: normal; }}
  /* —— 写入界面 —— */
  .bar {{ background: #f1efe8; border-radius: 8px; padding: 8px 12px;
          font-size: 12px; color: #5f5e5a; display: flex; gap: 10px;
          align-items: center; flex-wrap: wrap; margin-bottom: 16px; }}
  .bar input {{ font: inherit; padding: 3px 8px; border: 1px solid #c8c6be;
                border-radius: 6px; width: 130px; }}
  form {{ margin: 12px 0; }}
  textarea {{ font: inherit; font-size: 14px; width: 100%; box-sizing: border-box;
              padding: 10px 12px; border: 1px solid #c8c6be; border-radius: 8px;
              min-height: 76px; resize: vertical; background: #fff; }}
  input[type=text] {{ font: inherit; padding: 8px 10px; border: 1px solid #c8c6be;
                      border-radius: 8px; width: 100%; box-sizing: border-box;
                      background: #fff; }}
  button.go {{ font: inherit; font-size: 13px; padding: 7px 18px; cursor: pointer;
               border: 1px solid #185fa5; background: #185fa5; color: #fff;
               border-radius: 8px; }}
  button.ghost {{ font: inherit; font-size: 12px; padding: 5px 14px; cursor: pointer;
                  border: 1px solid #c8c6be; background: #fff; color: #444441;
                  border-radius: 8px; }}
  button.pick {{ font: inherit; font-size: 13px; padding: 6px 14px; cursor: pointer;
                 border: 1px solid #c8c6be; background: #fff; color: #444441;
                 border-radius: 8px; }}
  button.pick[aria-pressed=true] {{ border-color: #185fa5; background: #e6f1fb;
                                    color: #185fa5; }}
  .answer {{ display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap;
             align-items: center; }}
  .answer .lbl {{ font-size: 12px; color: #5f5e5a; }}
  .unanswered {{ border-left: 3px solid #854f0b; }}
  .ok {{ background: #e8f4e6; border-left: 3px solid #2e7d32;
         padding: 10px 14px; margin: 16px 0; font-size: 13px; }}
  .typepick {{ font-size: 12px; margin-top: 8px; }}
  .typepick select {{ font: inherit; font-size: 12px; padding: 3px 6px;
                      border: 1px solid #c8c6be; border-radius: 6px; background: #fff; }}
</style>
</head>
<body><div class="wrap">
{body}
<footer>
  看：<code>§C2.5</code> 第 3 档（只读派生视图）·
  写：走 <code>cli.py</code> 调的那同一批函数（<code>§14</code>）·
  命令行入口仍在 <code>python cli.py</code>
</footer>
</div></body></html>
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def _md(s) -> str:
    """把 `**强调**` 渲染成 <strong>。**先转义、再替换**，顺序不能反。

    备注字段本来是给终端看的 Markdown。网页里直接印会露出一串星号，
    所以这里做最小渲染 —— 只认 `**…**` 这一种，不做完整 Markdown
    （本仓库不引第三方 Markdown 库，B7 的硬墙）。
    """
    t = html.escape(str(s), quote=True)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


def _fmt_value(raw) -> str:
    """读数：能算出来的按小数印（限 4 位）。

    ⚠️ **`0.0` 是「能算出来，值是零」，不是「算不出」。** 所以这里只按
    `None` 判断，不能用 `if not raw` —— 那会把真的零吞成「—」，
    正好犯了这个项目最在意的那类错（把「测不了」和「零」混成一个）。
    """
    if raw is None or raw == "":
        return "—"
    try:
        return f"{float(raw):.4f}"
    except (TypeError, ValueError):
        return esc(raw)


def render_index(conn: sqlite3.Connection, who: str = "", msg: str = "") -> str:
    rows = _list_debates(conn)
    if not rows:
        inner = (
            '<p class="empty">这个库里还没有讨论。</p>'
        )
    else:
        cards = []
        for r in rows:
            cards.append(
                f'<div class="card">'
                f'<a href="/debate/{esc(r["id"])}"><strong>{esc(r["id"])}</strong></a>'
                f'<span class="badge">{esc(r["state"])}</span>'
                f'<div class="meta">建于 {esc(r["created_at"])}</div>'
                f"</div>"
            )
        inner = "".join(cards)
    body = (
        _who_bar(who)
        + msg
        + "<header><h1>Arena</h1>"
        '<div class="sub">把一群人吵出结构，而不是吵出胜负。</div></header>'
        '<div class="notice">AI 是 Secretary / Organizer / Detector，'
        "<strong>不是 Judge</strong>。它只做三件事：切分、组织、发现分歧。</div>"
        + render_write_form("new")
        + "<h2>全部讨论</h2>" + inner
    )
    return PAGE.format(title="Arena", body=body)


def _who_bar(who: str) -> str:
    """顶部那条身份栏。**身份走 Cookie，不走表单隐藏字段** ——
    隐藏字段每次提交都能改，而「谁提交的」一旦可伪造，
    `§C2.4` 按人算的那些观测点就没意义了。"""
    shown = esc(who) if who else ""
    mark = "" if who else "（还没填 —— 写入会被拒）"
    return (
        f'<div class="bar"><span>我是：</span>'
        f'<input id="who" value="{shown}" placeholder="你的名字">'
        f'<button class="ghost" type="button" onclick="saveWho()">记住</button>'
        f'<span id="whostate">{mark}</span></div>'
        "<script>"
        "function saveWho(){"
        "  var v=document.getElementById('who').value.trim();"
        "  document.cookie='arena_who='+encodeURIComponent(v)+';path=/;max-age=31536000';"
        "  location.reload();"
        "}"
        "</script>"
    )


def _claim_card(c: dict) -> str:
    """一条命题的卡片 —— Topic 直挂 / Position 下**共用一份**。

    两处各写一遍的话，以后加一样东西必然只改一处（和 `debate._claim_detail` 同一条理由）。
    """
    cl = c["claim"]
    text = ""
    for h in c.get("heads", []):
        try:
            payload = json.loads(h["content"])
        except (ValueError, TypeError):
            payload = {}
        text = payload.get("text") or h["content"]
        break
    bits = [
        f'<div class="card">'
        f'<span class="id">{esc(cl["id"])}</span>'
        f'<span class="badge">{esc(cl["status"])}</span>'
        f'<div class="txt">{esc(text)}</div>'
    ]
    edges = c.get("edges") or c.get("relations") or []
    for e in edges:
        kind = e.get("kind", "")
        to = e.get("to") or e.get("target") or ""
        bits.append(
            f'<div class="edge"><span class="k">{esc(kind)}</span> → '
            f"{esc(to)}</div>"
        )
    hint = c.get("type_note") or c.get("note")
    if hint:
        bits.append(f'<div class="meta">{esc(hint)}</div>')
    bits.append("</div>")
    return "".join(bits)


def _position_block(p: dict) -> str:
    """一个立场 + 它下面的命题。`§C4` 的 `Topic ├── Position ├── Claim`。"""
    pos = p["position"]
    text = ""
    for h in p.get("heads", []):
        try:
            payload = json.loads(h["content"])
        except (ValueError, TypeError):
            payload = {}
        text = payload.get("text") or h["content"]
        break
    out = [
        f'<div class="stance">'
        f'<div class="stancetop"><span class="id">{esc(pos["id"])}</span>'
        f'<span class="badge">立场</span>'
        f'<strong class="stancetxt">{esc(text)}</strong></div>'
    ]
    if not p.get("claims"):
        out.append('<p class="empty">这个立场下还没有命题。</p>')
    out.append("".join(_claim_card(c) for c in p.get("claims", [])))
    out.append("</div>")
    return "".join(out)


def render_debate(conn: sqlite3.Connection, debate_id: str) -> str:
    v = debate.view(conn, debate_id)
    d = v["debate"]
    parts = [
        f'<div class="eyebrow"><a href="/">← 全部讨论</a></div>',
        f"<header><h1>{esc(d['id'])}</h1>"
        f'<div class="sub">状态 {esc(d["state"])} · 提出者 {esc(d["origin"])} · '
        f"建于 {esc(d['created_at'])}</div></header>",
        # 上层的入口。放在**页面上部**，因为它读的是这个讨论的**整体结构**，
        # 而这一页其余部分都是逐条平铺的 —— 混在里面会被当成又一条命题。
        f'<div class="notice"><a href="/upper/{esc(debate_id)}">上层归纳 →</a>'
        "（它只影响展示顺序 / 默认展开 / 推荐候选，"
        "没有说任何一条命题「对」）</div>",
    ]
    for t in v["topics"]:
        topic = t["topic"]
        parts.append(f"<h2>{esc(topic['id'])}</h2>")
        positions = t.get("positions", [])
        claims = t.get("claims", [])
        if not positions and not claims:
            parts.append('<p class="empty">这个话题下还没有命题。</p>')
        for p in positions:
            parts.append(_position_block(p))
        if claims:
            if positions:
                parts.append(
                    '<div class="notice">下面这些命题<strong>直挂在话题下</strong>。'
                    "它们同时也属于上面的某个立场 —— <strong>并挂，不是搬家</strong>"
                    "（<code>§C10</code> 只进不退：命题属于哪段原文这件事实仍然查得到）。"
                    "</div>"
                )
            parts.extend(_claim_card(c) for c in claims)
        parts.append(
            '<div class="notice warn">⚠️ 上面没有任何「哪条更重要」。'
            "本系统不产生那个量（<code>§C6.1</code>）。</div>"
        )
    parts.append(render_write_form("submit", debate_id=debate_id))
    return PAGE.format(title=f"{debate_id} · Arena", body="".join(parts))


def render_observe(conn: sqlite3.Connection) -> str:
    snap = observe.snapshot(conn)
    rows = []
    for key, e in snap.items():
        num = e.get("分子", "")
        den = e.get("分母", "")
        can = str(e.get("算得出", "")).lower() == "true"
        why = e.get("分子产不出", "")
        note = e.get("备注", "")
        if can:
            reading = _fmt_value(e.get("值", ""))
        elif why:
            # 分子产生不出来 —— **不是零，是测不了**。原因照原话印，不吞掉。
            reading = f'<span class="na">算不出</span><br><span class="k">{_md(why)[:150]}</span>'
        else:
            reading = '<span class="na">算不出</span><br><span class="k">分母为 0（还没开始记）</span>'
        rows.append(
            "<tr>"
            f'<td><strong>{_md(e.get("观测点", key))}</strong><br>'
            f'<span class="k">{esc(e.get("层", ""))} · {esc(e.get("条款", ""))}</span></td>'
            f"<td>{reading}<br>"
            f'<span class="k">{esc(num)} / {esc(den)}</span></td>'
            f"<td style='font-size:12px'>{_md(note)[:240]}</td>"
            "</tr>"
        )
    body = (
        "<header><h1>观测点</h1>"
        f'<div class="sub">共 {len(snap)} 个 · <code>§C7.2</code></div></header>'
        '<div class="notice">下面所有的数<strong>没有高低之分</strong>。'
        "这里不说什么叫「高」，也不会有。<br>"
        "「算不出」有<strong>两个</strong>来源，分开写：分母为 0（还没开始记）"
        "vs 分子产生不出来（<strong>是测不了，不是零</strong>）。</div>"
        "<table><tr><th>观测点</th><th>读数</th><th>备注</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return PAGE.format(title="观测点 · Arena", body=body)


def render_chart(conn: sqlite3.Connection, topic_id: str) -> str:
    txt = vote.chart(conn, topic_id)
    tally = vote.tally(conn, topic_id)
    rows = []
    for cid, s in (tally.get("argument_support") or {}).items():
        rows.append(
            f"<tr><td>{esc(cid)}</td><td>{esc(s.get('supports'))}</td>"
            f"<td>{esc(s.get('contradicts'))}</td>"
            f"<td>{esc(s.get('qualifies'))}</td></tr>"
        )
    support = (
        "<table><tr><th>命题</th><th>支持</th><th>反对</th><th>限定</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else '<p class="empty">还没有可看的命题。</p>'
    )
    body = (
        '<div class="eyebrow"><a href="/">← 全部讨论</a></div>'
        f"<header><h1>票数 · {esc(topic_id)}</h1></header>"
        '<div class="notice warn"><code>§C12.1</code>：下面是<strong>公共偏好</strong>，'
        "不是真理判定。公众倾向高<strong>不等于</strong>更正确。</div>"
        f"<pre>{esc(txt)}</pre>"
        "<h2>四个量分开看</h2>"
        '<div class="notice">四项<strong>是四件事，不许合成一句话</strong>'
        "（<code>§C12.2</code>）。</div>" + support
    )
    return PAGE.format(title=f"{topic_id} 票数 · Arena", body=body)


def render_upper(conn: sqlite3.Connection, debate_id: str) -> str:
    """上层归纳的**看**那一面 —— 只读，同 `render_observe` 一个规矩。

    只调度 `upper.scan()` / `upper.count_signals()`，**不在这里算任何东西** ——
    同本文件开头那条：界面层多写一行判断，就等于多一条写路径。

    ⚠️ 三件事必须原样印出来，缺一件这个页面就会骗人：

    1. **上层只影响三件事**（展示顺序 / 默认展开 / 推荐候选），
       且它一个字都没写进底层（`§C7.1` ④）；
    2. **「还没有」和「算不出来」分开** —— 同 `observe.py` 的「算不出 ≠ 零」，
       也同 `stop.py` 的 `not_judged`；
    3. **这个模块看不见什么**（`upper.BLIND_SPOTS`）。不印这一段的话，
       读者会把「没看见」当成「不存在」。
    """
    s = upper.scan(conn, debate_id)
    sig = upper.count_signals(conn)

    parts = [
        f'<div class="eyebrow"><a href="/debate/{esc(debate_id)}">← 回讨论</a></div>',
        f"<header><h1>上层上下文 · {esc(debate_id)}</h1>"
        f'<div class="sub">规则集 <code>{esc(s["version"])}</code> · '
        "<code>§C7.1</code>（MVP 阶段本机制按留白项处理）</div></header>",
        '<div class="notice">上层只影响<strong>三件事</strong>：'
        "展示顺序 / 默认展开 / 推荐候选。它没有说任何一条命题「对」，"
        "也<strong>一个字都没写进底层</strong>（<code>§C7.1</code> ④）。</div>",
    ]

    # --- 输入：三个结构量（是原料，不是名次）---
    rows = []
    for key, hits in sig.items():
        if hits:
            head = list(hits.items())[:8]
            detail = "、".join(f"<code>{esc(k)}</code>×{esc(v)}" for k, v in head)
            if len(hits) > len(head):
                detail += f' <span class="k">（共 {len(hits)} 条）</span>'
        else:
            detail = '<span class="empty">没有</span>'
        rows.append(f"<tr><td><code>{esc(key)}</code></td><td>{detail}</td></tr>")
    parts.append(
        "<h2>输入：三个结构量</h2>"
        '<div class="notice">这三个是<strong>原料</strong>，不是名次 —— '
        "「多少次算多」不在这里判。<br>"
        "热度类信号（票数 / 浏览 / 共识）<strong>一个都不读</strong>"
        "（<code>§C7.1</code> ①，B4 盯着）。</div>"
        '<table><tr><th>信号</th><th>读数</th></tr>' + "".join(rows) + "</table>"
    )

    # --- 上下文 ---
    if s["contexts"]:
        cards = []
        for c in s["contexts"]:
            badge = ("" if c["named"]
                     else '<span class="badge warn">名字由人来给</span>')
            ev = c["evidence_ids"] or []
            # `signal` 可能没记 —— 印「未记」而不是 `None`：
            # 「没记」和「记了空值」不是一回事（同 `_fmt_value` 那条规矩）。
            sig_cell = (f'<code>{esc(c["signal"])}</code>' if c["signal"]
                        else '<span class="empty">未记</span>')
            cards.append(
                f'<div class="card"><div class="id">{esc(c["context"]["id"])}</div>'
                f'<p class="txt">{_md(c["name"])}{badge}</p>'
                f'<div class="meta">信号 {sig_cell} · 依据 {len(ev)} 条</div>'
                f'<div class="edge">{esc("、".join(ev))}</div></div>'
            )
        parts.append("<h2>上下文</h2>" + "".join(cards))
    else:
        why = s["empty_reason"] or "底层的结构性信号还没攒够"
        parts.append(
            "<h2>上下文</h2>"
            '<p class="empty">还没有上层节点。</p>'
            '<div class="notice warn">⚠️ 「<strong>还没有</strong>」和'
            "「<strong>算不出来</strong>」不是一回事，所以两句都印：<br>"
            f"· <strong>还没有</strong> —— {esc(why)}<br>"
            "· <strong>算不出来</strong> —— 本模块看不见某种信号（见下）</div>"
        )

    # --- 盲区 ---
    parts.append(
        "<h2>这个模块看不见什么</h2>"
        '<div class="notice warn">不印这一段的话，读者会把「没看见」'
        "当成「不存在」。</div>"
        "<ul>" + "".join(f"<li>{esc(b)}</li>" for b in s["blind_spots"]) + "</ul>"
    )
    return PAGE.format(title=f"上层上下文 · {debate_id} · Arena",
                       body="".join(parts))


class Server(ThreadingHTTPServer):
    """⚠️ `allow_reuse_address = False` 在 Windows 上是**必需的**，不是洁癖。

    标准库默认是 `1`。在 POSIX 上它只表示「允许复用 `TIME_WAIT` 的地址」，
    重启服务不会被上一次的残留 socket 挡住 —— 那里它是有用的。

    **但在 Windows 上 `SO_REUSEADDR` 的语义不一样**：它允许第二个 socket
    **抢占一个已经处于 LISTEN 状态的端口**。实测（Python 3.13 / Windows）：

        s1.bind(("127.0.0.1", P)); s1.listen(5)   # 第一个实例
        s2.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        s2.bind(("127.0.0.1", P)); s2.listen(5)   # ← 不报错，两个都在监听

    后果：起第二个 `serve.py` 时 `bind()` 不抛 `OSError`，
    于是 `main()` 里那条 `except OSError` **永远不会走到**，
    两个进程同时监听一个端口、谁来接连接看运气 ——
    用户看到的现象是「明明起了新版，页面却还是旧版，写入表单没了」。

    关掉它之后，第二个实例会在 `bind()` 处老实抛 `OSError`，
    被 `main()` 接住并大声报错。**代价**：Windows 上重启服务时，
    若上一次的 TCP 残留还在，可能要等几秒或换端口 —— 这个代价是值得付的，
    因为「悄悄跑起来两个服务」比「多等两秒」危险得多。
    """

    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "Arena/2.0"

    def _send(self, content: str, code: int = 200) -> None:
        raw = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))

    def _read_form(self) -> dict[str, list[str]]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "replace")
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    def _redirect(self, where: str) -> None:
        """写完一律重定向 —— 不重定向的话，用户按 F5 会再写一次。"""
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _fail(self, msg: str, where: str = "/") -> None:
        """写入失败 —— 印成一个页面，**不在控制台吞掉**。"""
        self._send(
            PAGE.format(
                title="写不进去",
                body="<header><h1>写不进去</h1></header>"
                f'<div class="notice warn">{_md(msg)}</div>'
                f'<p><a href="{esc(where)}">← 回去</a></p>',
            ),
            400,
        )

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        # ⚠️ **最外层兜底。** 少了它，任何一条路由漏出的异常都会让
        # `ThreadingHTTPServer` 只在**服务端控制台**打一段 traceback，
        # 而浏览器收到的是**空响应**（curl 报 `HTTP 000`）—— 用户看到的
        # 就是「打不开」，且原因在另一个窗口里。错误必须印在他看得见的地方。
        try:
            self._route(path)
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            try:
                self._send(
                    PAGE.format(
                        title="界面出错了",
                        body="<header><h1>界面出错了</h1></header>"
                        f'<div class="notice warn"><strong>{esc(type(e).__name__)}</strong>'
                        f"：{esc(e)}</div>"
                        "<h2>往哪儿看</h2>"
                        f"<p>库文件：<code>{esc(DEFAULT_DB)}</code><br>"
                        "1. 库里还没有讨论 → 在首页直接开一个，或用命令行：<br>"
                        "<code>python cli.py init</code><br>"
                        '<code>python cli.py new "&lt;你的问题&gt;" &lt;谁&gt;</code><br>'
                        "<code>python cli.py submit &lt;debate-id&gt; &lt;谁&gt; "
                        "&quot;&lt;你的原话&gt;&quot;</code><br>"
                        "<code>python cli.py confirm &lt;debate-id&gt; "
                        "&lt;draft-id&gt; &lt;谁&gt;</code></p>"
                        '<p><a href="/">← 重试</a></p>',
                    ),
                    500,
                )
            except Exception:  # noqa: BLE001
                pass  # 连错误页都发不出去时，控制台那份 traceback 是最后的记录

    def _route(self, path: str) -> None:
        conn = _open()
        try:
            if path == "/":
                self._send(render_index(conn, who=_who_of(self)))
            elif path.startswith("/confirm/"):
                rest = path[len("/confirm/") :]
                did, _, draft = rest.partition("/")
                try:
                    self._send(render_confirm(
                        conn, urllib.parse.unquote(did),
                        urllib.parse.unquote(draft), _who_of(self),
                    ))
                except Exception as e:  # noqa: BLE001
                    self._send(
                        PAGE.format(
                            title="看不了这份 draft",
                            body=f"<header><h1>看不了这份 draft</h1></header>"
                            f'<div class="notice warn">{esc(e)}</div>'
                            f'<p><a href="/">← 回全部讨论</a></p>',
                        ),
                        404,
                    )
            elif path.startswith("/debate/"):
                did = urllib.parse.unquote(path[len("/debate/") :])
                try:
                    self._send(render_debate(conn, did))
                except Exception as e:  # noqa: BLE001
                    self._send(
                        PAGE.format(
                            title="看不了",
                            body=f"<header><h1>看不了 {esc(did)}</h1></header>"
                            f'<div class="notice warn">{esc(e)}<br><br>'
                            f'<a href="/">← 回全部讨论</a></div>',
                        ),
                        404,
                    )
            elif path == "/observe":
                self._send(render_observe(conn))
            elif path.startswith("/chart/"):
                tid = urllib.parse.unquote(path[len("/chart/") :])
                self._send(render_chart(conn, tid))
            elif path.startswith("/upper/"):
                did = urllib.parse.unquote(path[len("/upper/") :])
                self._send(render_upper(conn, did))
            else:
                self._send(
                    PAGE.format(
                        title="没有这个页面",
                        body="<header><h1>没有这个页面</h1></header>"
                        f"<p><code>{esc(path)}</code></p>"
                        '<p><a href="/">← 回全部讨论</a></p>',
                    ),
                    404,
                )
        finally:
            conn.close()

    # ---- 写 ---- 四个动作，各自只调一个已有的库函数。

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            self._post(path)
        except scaffold.ScaffoldError as e:
            # 库层拒绝 —— **照原话印**，不包装成「出错了」。这是设计的一部分：
            # `§C5` 的「不回答不写」「挑子集等于替别人做决定」都要被看见。
            self._fail(str(e), "/")
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self._fail(f"{type(e).__name__}：{e}", "/")

    def _post(self, path: str) -> None:
        conn = _open()
        try:
            form = self._read_form()
            who = _who_of(self)
            back = (form.get("debate_id", [""])[0] or "/").strip() or "/"
            back = f"/debate/{urllib.parse.quote(back)}" if back != "/" else "/"

            if not who:
                self._fail(
                    "**还不知道你是谁。** 先在页面顶部填一个名字、点「记住」，"
                    "再写。写入要记「谁提出来的」（`§C2.4` 的观测点按人算），"
                    "这个不能空。",
                    back,
                )
                return

            if path == "/new":
                out = act_new(conn, form, who)
                self._send(PAGE.format(
                    title="已建讨论",
                    body=_who_bar(who) + out
                    + '<p><a href="/">← 回全部讨论</a></p>',
                ))
            elif path == "/submit":
                self._send(PAGE.format(
                    title="已切分",
                    body=_who_bar(who) + act_submit(conn, form, who)
                    + f'<p><a href="{esc(back)}">← 回讨论</a></p>',
                ))
            elif path == "/rewrite":
                self._send(PAGE.format(
                    title="已重写",
                    body=_who_bar(who) + act_rewrite(conn, form, who)
                    + f'<p><a href="{esc(back)}">← 回讨论</a></p>',
                ))
            elif path == "/abandon":
                self._send(PAGE.format(
                    title="已记下不提交",
                    body=_who_bar(who) + act_abandon(conn, form, who)
                    + f'<p><a href="{esc(back)}">← 回讨论</a></p>',
                ))
            elif path == "/confirm":
                self._send(PAGE.format(
                    title="确认结果",
                    body=_who_bar(who) + act_confirm(conn, form, who),
                ))
            else:
                self._fail(f"没有这个写入入口：{path}", back)
        finally:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    global DEFAULT_DB  # 必须在**首次用到这个名字之前**声明，否则 SyntaxError
    force_utf8()
    ap = argparse.ArgumentParser(
        prog="serve.py",
        description="Arena 的网页界面（零依赖，只用标准库）",
    )
    ap.add_argument("--host", default="127.0.0.1", help="默认 127.0.0.1（只本机能访问）")
    ap.add_argument("--port", type=int, default=8765, help="默认 8765")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"默认 {DEFAULT_DB}")
    args = ap.parse_args(argv)

    DEFAULT_DB = args.db

    # ⚠️ 端口被占时必须**大声失败**。第一版这里直接构造 `ThreadingHTTPServer`，
    # 绑不上就抛 `OSError` 退出 —— 而 `.bat` 是老代码，只看「端口通不通」，
    # 于是它报告成功、打开浏览器，指向的是**上一个**还活着的旧实例。
    # 用户看到的就是「新功能没了」。这里把话说清楚，别让下一个人猜。
    #
    # 光加 `except` 还不够 —— 标准库默认开着 `SO_REUSEADDR`，在 Windows 上
    # 它允许第二个 socket **抢占已监听的端口**，于是 bind() 根本不报错。
    # 真正让这段 `except` 生效的是上面 `Server.allow_reuse_address = False`。
    try:
        httpd = Server((args.host, args.port), Handler)
    except OSError as e:
        print(f"\n[错] 绑不上 {args.host}:{args.port} —— {e}")
        print()
        print("这个端口上已经有一个服务在跑了（多半是上一次没关干净的自己）。")
        print("两条路：")
        print(f"  1. 换个端口：python serve.py --port {args.port + 1}")
        print("  2. 先清掉旧的：")
        print("     Windows:  netstat -ano | findstr :%d  然后 taskkill /F /PID <pid>"
              % args.port)
        print(f"     Git Bash: taskkill //F //IM python.exe")
        return 1

    print(f"Arena → http://{args.host}:{args.port}")
    print(f"库：{DEFAULT_DB}")
    print("看：只读派生视图。写：走 cli.py 调的那同一批函数。")
    print("没有接的是投票的「投」（§C12 / §13.5）。")
    print("Ctrl-C 停下。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
