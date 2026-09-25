"""把标签文件的 47 个节点文本对回各自的输入 —— 看 `boundaries` 折不折得出来。

    python samples/align.py

**它做两件事**：① 逐节点给覆盖率，看哪些能折出边界；② 核「原文未出现」标记
是不是符合判据（判据见下面 `ABSENT_MARK` 那段）—— 那个标记是 `proposed_count`
的输入，它漂了值就跟着漂，而且看不出来。

**为什么要有这个文件**：`boundaries` 一直是空的，理由是「标签节点文本是转述不是原文
切片」。那句话是我说的，它**没有被量过**。这个脚本就是量它的工具，用的是标准库
成熟的序列对齐方案 `difflib.SequenceMatcher`（`autojunk=False`）。

    https://docs.python.org/3/library/difflib.html

**它测得出什么、测不出什么**（文档自己标的边界，我照抄，不替它吹）：

- 它对齐的是**精确序列相似度**，不是**意义**。改写重的地方，字符对不上就是低分，
  哪怕说得完全是一回事。所以下面的「覆盖率」**不是**「转述得准不准」，
  只是「有多少字是原样搬过来的」。
- 中文按**字符**对齐（不分词）。单字块遍地都是，所以这里只认长度 ≥ `MIN_RUN`
  的连续块 —— 见下面的注释。

**输出的是事实，不是结论。** 填不填 `boundaries` 由看这份输出的人定。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "samples" / "inputs.md"
LABELS = ROOT.parent / "outputs" / "Arena-MVP-测试样本-标签.md"

# 只认长度 ≥ 3 的连续匹配块。
#
# 这是个**实现细节上的自选**（`§T4.1` A 类），说清理由：中文按字符对齐时，
# 「的」「了」「是」这类字会到处命中，一堆长度 1 的块能把任何两句话连成一大片，
# 那样量出来的「覆盖率」只反映常用字有多少，不反映搬了多少原文。
# 长度 3 的中文串连续命中原样，偶然撞上的可能已经很低。
# **换掉这个数会改变下面所有数字** —— 所以它写在这里，不藏在函数体里。
MIN_RUN = 3

# 「这个节点不是一段原文」的标记。命中就不参与折算 —— **不是**折算失败，
# 是它本来就不该有边界：
#   `n1 导致 n2`、`职业教育 → 工人水平`  是**关系**，不是一个文本区间
#   `数据是三年前的 + 情况已变`           是**两个区间合起来**的节点
_NODE_REF = re.compile(r"\bn\d\b|→|\+")

_MD = re.compile(r"[*`]")
_INPUT_HEAD = re.compile(r"^## (\d{4})\s*$", re.M)
_INPUT_LINE = re.compile(r'^input:\s*"(.*)"\s*$', re.M)
_LABEL_HEAD = re.compile(r"^### ([A-F]\d) · (.+?)\s*$", re.M)
_NODE_ROW = re.compile(
    r"^\|\s*(n\d+)\s*\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*$", re.M)
_NOT_IN_SOURCE = re.compile(r"（原文未出现）|\(原文未出现\)")


def inputs() -> list[tuple[str, str]]:
    """`inputs.md` → [(编号, 输入原文)]，按文件顺序。"""
    text = INPUTS.read_text(encoding="utf-8")
    heads = list(_INPUT_HEAD.finditer(text))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end]
        m = _INPUT_LINE.search(body)
        if m:
            out.append((h.group(1), m.group(1)))
    return out


def labelled() -> list[tuple[str, str, list[tuple[str, str, str, bool]]]]:
    """标签文件 → [(节号, 标题, [(节点号, 标签, 内容, 是否标着原文未出现)])]。"""
    text = LABELS.read_text(encoding="utf-8")
    heads = list(_LABEL_HEAD.finditer(text))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        rows = []
        for m in _NODE_ROW.finditer(text[h.end():end]):
            raw = m.group(3)
            rows.append((m.group(1), m.group(2),
                         _MD.sub("", _NOT_IN_SOURCE.sub("", raw)).strip(),
                         bool(_NOT_IN_SOURCE.search(raw))))
        out.append((h.group(1), h.group(2), rows))
    return out


def align(source: str, node_text: str) -> dict:
    """一个节点文本 → 输入原文里它搬自哪儿。

    返回的 `span` 是**搬过来的那些字**在原文里的最小外包区间，
    **不是**这个节点的边界 —— 转述掉的部分不在里面，那些位置没有任何证据。
    """
    sm = difflib.SequenceMatcher(None, source, node_text, autojunk=False)
    runs = [b for b in sm.get_matching_blocks() if b.size >= MIN_RUN]
    covered = sum(b.size for b in runs)
    return {
        "span": (min(b.a for b in runs), max(b.a + b.size for b in runs)) if runs else None,
        "covered": covered,
        "coverage": covered / len(node_text) if node_text else 0.0,
        "runs": len(runs),
        "gaps": max(0, len(runs) - 1),
    }


def classify(source: str, node_text: str) -> tuple[str, dict | None]:
    """一个节点 → (它算什么, 对齐结果或 None)。

    四种，**只有第一种能折出边界**：

    - `逐字`   节点文本的**每一个字**都原样出现在原文里、且顺序一致 → 它的 span
               就是原文里那一段，可直接填。**允许中间有洞** —— 0002 是
               「大学文凭的回报~~这几年~~在下降」，把「这几年」删掉，
               每个字都还是原文的，span 也还是原文那一段。**删不是改写。**
               判据因此是 `coverage == 1.0`，不是「只有一段」——
               要求连续会把 0002 这种**没有任何改写**的也误判成折不出来。
    - `转述`   有原文的片段，但节点文本里有字是原文里没有的 → span 是我推的，不是原文
    - `非文本` 关系节点 / 合并节点 —— 本来就跨多个区间，没有单一边界
    - `对不上` 一个字都搬不过来（多半是被彻底改写了）
    """
    if _NODE_REF.search(node_text):
        return "非文本", None
    a = align(source, node_text)
    if a["coverage"] >= 1.0:
        return "逐字", a
    return ("对不上" if a["coverage"] == 0.0 else "转述"), a


# 「原文未出现」这个标记的判据（2026-09-25 需求方定）。`main()` 会逐节点核它。
#
#   标「原文未出现」 ⟺ 类型是 `Assumption`，且文本在原文里一个字都搬不过来。
#
# 两条都要，缺一条就会改错 —— 下面两个反例都是**量过**的：
#
# - 只用 `coverage == 0`：会把**重转述**和**括号注释**误算成「原文没有」。
#   0007 B4 的 n2「原因是聪明」/ n3「原因不是努力」覆盖率 0%，可标签文件 `:91`
#   明写「n3 是独立命题」；0019 F2 n2、0020 F3 n2 覆盖率也是 0%，那是括号注释。
#   覆盖率量的是**搬了多少字**，不是**内容在不在** —— 两者都 0%，它分不开。
# - 只用「是不是关系节点」：0004 B1 的 n3「n1 导致 n2」判 `非文本`，
#   可「所以」实实在在占 [11:13]；0016 E2 的 n2 含 `+` 也判 `非文本`，
#   而它的两个半句都在原文里。
ABSENT_MARK = "原文未出现"


def marker_should_be(label: str, kind: str, a: dict | None) -> bool:
    """这个节点**该不该**带「原文未出现」标记。判据见上面那段注释。"""
    return label == "Assumption" and a is not None and a["coverage"] == 0.0


def marker_mismatches() -> tuple[int, list[str]]:
    """(标记总数, 与判据不符的逐条说明)。

    不符分两种，都要报：**该标没标**、**不该标却标了**。
    这个标记是 `proposed_count` 的输入，它漂了 `proposed_count` 跟着漂，而且看不出来。
    """
    total, bad = 0, []
    for (num, source), (sec, _t, rows) in zip(inputs(), labelled()):
        for nid, label, content, absent in rows:
            if absent:
                total += 1
            kind, a = classify(source, content)
            if marker_should_be(label, kind, a) == absent:
                continue
            cov = f"{a['coverage']:.0%}" if a else "—"
            bad.append(f"{num} {sec} {nid}（{label}）"
                       f"{'标了' if absent else '没标'}，"
                       f"{'该标' if not absent else '不该标'} —— "
                       f"实测 {kind}／覆盖 {cov}")
    return total, bad


def main() -> None:
    ins = inputs()
    labs = labelled()
    print(f"输入 {len(ins)} 条，标签 {len(labs)} 节，MIN_RUN={MIN_RUN}\n")
    tally = {"逐字": 0, "转述": 0, "非文本": 0, "对不上": 0}
    absent_n = 0
    foldable = []
    samples = []

    for (num, source), (sec, title, rows) in zip(ins, labs):
        print(f"── {num}  {sec}  「{source}」")
        spans, blockers = [], []
        for nid, label, content, absent in rows:
            if absent:
                absent_n += 1
                print(f"   {nid:3} {label:17} 标着「原文未出现」—— 结构上不该有边界")
                continue
            kind, a = classify(source, content)
            tally[kind] += 1
            span = (f"[{a['span'][0]}:{a['span'][1]}]"
                    if a and a["span"] else "（对不上）")
            print(f"   {nid:3} {label:17} {kind}  覆盖 "
                  f"{a['coverage']:5.1%}  搬自 {span}" if a else
                  f"   {nid:3} {label:17} {kind}")
            print(f"       └ 节点文本：{content}")
            if kind == "逐字":
                spans.append((nid, a["span"]))
            elif kind != "非文本":
                blockers.append(f"{nid}(覆盖{a['coverage']:.0%})")
            # `非文本` 不算阻碍：关系/合并节点**本来就没有单一边界**，空是对的。
            # `对不上` 必须算阻碍 —— 它照样是标签里的一条命题，给不出它的边界，
            # 这一条的 boundaries 就是**不全**的，而不全的列表读起来像全的。
        ok = not blockers
        foldable.append((num, ok))
        samples.append((num, sec, [s for _, s in spans]))
        print(f"   ⇒ {'**可折**' if ok else '不可折'} —— "
              f"{'全部内容节点逐字' if ok else '卡在 ' + '、'.join(blockers)}\n")

    print("=" * 72)
    print(f"节点总数 {sum(tally.values()) + absent_n}")
    for k, v in tally.items():
        print(f"  {k:5} {v}")
    print(f"  {'原文未出现':5} {absent_n}（不参与折算，不是失败）")

    total, bad = marker_mismatches()
    if bad:
        print(f"\n⚠️ 「{ABSENT_MARK}」标记与判据不符 {len(bad)} 处"
              f"（共 {total} 处标记）：")
        for line in bad:
            print(f"   {line}")
        print(f"   判据：类型是 `Assumption` 且覆盖率 0 —— 见 align.py 里那段注释。")
    else:
        print(f"  ⇒ 「{ABSENT_MARK}」{total} 处标记**全部符合判据**"
              f"（类型是 `Assumption` + 覆盖率 0）")

    print(f"\n可折出 boundaries 的样本：{sum(1 for _, ok in foldable if ok)} / 20"
          f"  →  {' '.join(n for n, ok in foldable if ok) or '无'}")
    print(f"不可折：{' '.join(n for n, ok in foldable if not ok)}")
    print("\n可折样本的边界（节点顺序，未必等于原文顺序）：")
    for num, sec, spans in samples:
        if any(n == num for n, ok in foldable if ok):
            print(f"  {num}  {sec}  {spans}")


def write_back() -> None:
    """把算出来的结果写回 `inputs.md` 的 annotation 区块。

    值本身**不手抄** —— 手抄 20 条是三处打错的来源，而且下次报告和文件会对不上。
    这个函数只改两行：`boundaries` 和 `boundaries_basis`。
    **`proposed_count` 一个字都不动**：那是另一件事，别顺手改。
    """
    ins = inputs()
    labs = labelled()
    text = INPUTS.read_text(encoding="utf-8")
    heads = list(_INPUT_HEAD.finditer(text))
    out, cursor = [], 0
    for i, (num, source) in enumerate(ins):
        sec, _title, rows = labs[i]
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[heads[i].end():end]

        spans, blockers = [], []
        for nid, _l, content, absent in rows:
            if absent:
                continue
            kind, a = classify(source, content)
            if kind == "逐字":
                spans.append(list(a["span"]))
            elif kind != "非文本":
                blockers.append(f"{nid}(覆盖 {a['coverage']:.0%})")

        if blockers:
            new_b = "[]"
            # 空要**自己说清是哪种空**：不是「还没标」，是「折不出来」，
            # 而且卡在哪一条、差多少，都写在这行里。
            new_basis = (f"折不出来 —— 卡在 {'、'.join(blockers)}："
                         f"节点文本是转述不是原文切片，边界得推，"
                         f"推出来的是我的不是原文的（annotation-derived.md §2）")
        else:
            new_b = "[" + ", ".join(f"[{a}, {b}]" for a, b in spans) + "]"
            new_basis = (f"折算 —— 全部 {len(spans)} 个内容节点逐字出现在原文里，"
                         f"切片直接对上（samples/align.py，MIN_RUN={MIN_RUN}）")

        body = _rewrite_yaml_line(body, "boundaries", new_b)
        body = _rewrite_yaml_line(body, "boundaries_basis", new_basis)
        out.append(text[cursor:heads[i].end()])
        out.append(body)
        cursor = end
    out.append(text[cursor:])
    # `newline="\n"` 不能省。默认值在 Windows 上会把 `\n` 翻成 `\r\n`，
    # 于是这个「重新生成」的工具会把整份文件悄悄改成 CRLF，
    # 把 `.gitattributes` 钉的 LF 策略抹掉 —— 而且 git 只在提交时才警告。
    INPUTS.write_text("".join(out), encoding="utf-8", newline="\n")
    print(f"已写回 {INPUTS}")


def _rewrite_yaml_line(body: str, key: str, value: str) -> str:
    return re.sub(rf"^(\s*{key}):.*$", lambda m: f"{m.group(1)}: {value}",
                  body, count=1, flags=re.M)


if __name__ == "__main__":
    import sys
    if "--write" in sys.argv:
        write_back()
    else:
        main()
