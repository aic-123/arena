"""《选型声明》(`§T4.2`) 里承诺的可执行否证检查。

`§T4.2`：B 类的第 4 项不能只是说法，必须落成一条可执行的否证检查。
        **检查跑不通 = 声明不成立 = 停下提问。**

`§C13.B`：只有可执行的检查能挡住套话式自证。

    python checks.py          # 全过 → 退出码 0；有命中 → 退出码 1

--- 检查口径（写清楚，免得它变成走过场）------------------------------------

用 `tokenize` 定位注释位置，然后**逐行**扫，丢掉**注释和 docstring**。
字符串**字面量照扫** —— 所以把 `"score"` 写成 dict 的键也会被抓到。
docstring 为什么不扫，见 修订四。
被扫的是**产物代码**：`*.py`，含测试。测试里也不许出现这些标识符 ——
所以 `test_arena.py` 里方法名如果撞上，就改名，而不是把测试排除掉。

**只有两个例外**（`EXEMPT`）：本文件与 `test_checks.py` —— 它们是验伪装置本身，
不写违规词就干不了活。这个集合由 `test_checks.py` 里一条用例钉死。

**为什么是逐行而不是逐 token**：逐 token 时 `a['ratio'] > 0.3` 会被切成
`a` / `[` / `'ratio'` / `]` / `>` / `0.3` 六个 token，**任何跨 token 的写法都匹配不到**，
检查会静默空转。这一点是注入测试暴露出来的（见 DECLARATION.md §4）。
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from functools import partial
from pathlib import Path

ROOT = Path(__file__).parent

# 不在扫描范围内的**只有这两个文件**，因为它们是**验伪装置本身**：
#   checks.py       必须写出这些词，才能拿它们当检查用；
#   test_checks.py  必须写出真的违规语料，否则没法证明检查不是空转。
# 两个都是「不写违规就干不了活」的文件。**产物代码与产物测试照扫**（含 test_arena.py）。
# 这是个洞，所以钉住：test_checks.py 有一条用例断言这个集合**恰好**是这两个名字 ——
# 将来想多豁免一个，必须改那条用例，改的时候会被看见。
# 依据见 DECLARATION.md §4.3。
EXEMPT = {"checks.py", "test_checks.py"}

# (编号, 说明, 触发的正则, 触碰的条款)
CHECKS = [
    # `(?<!b)lock` 而不是 `lock`：`blocks` / `blocking` 里含 "lock" 是子串巧合，
    # 而 `deadlock` / `FileLock` / `flock` 是真锁，仍要抓到。
    ("B1", "不引入锁",
     r"(?<!b)lock|mutex|semaphore", "§C11.2 §C11.3"),
    ("B2", "无权重 / 评分 / 名次 / 真值",
     r"weight|score|rank|truth", "§C6.1 §C9 #4 #5 #7"),
    ("B3", "不实现自动分裂 / 自动合并 / 阈值",
     r"auto_split|auto_merge|threshold", "§C12.5 §C9 #9"),
    ("B4", "不做上层归纳（§C7.1 明示 MVP 全程休眠）",
     r"consensus|cluster|emergence", "§C7.1 §C9 #5"),
    ("B5", "无标量分",
     r"evidence_score|truth_score|total_score|verdict", "§C6.1 §C9 #7"),
    # `§T2` 第 12 步引入发号器之后新加的。它是 §C9 #10 的可执行形式：
    # 版本表只许加行，不许覆盖 —— `revision` 没有 UPDATE 路径，这条盯着它别长出来。
    ("B11", "版本表只增不改（无覆盖式更新）",
     r"UPDATE\s+revision\b", "§C10 §C9 #10"),
]

# B6 需要**跨行跟变量**，一行正则做不到，所以单独一个函数。见 check_no_threshold。
_METRIC_WORD = re.compile(r"(?:ratio|rate|duration|elapsed|cost)\w*", re.I)
_COMPARISON = re.compile(r"(?:>=|<=|>|<)\s*[0-9]")
_ASSIGNED = re.compile(r"^\s*(\w+)\s*=")

_TRIVIAL = (tokenize.NL, tokenize.COMMENT)


def _docstring_lines(src: str) -> set[int]:
    """docstring 占的行号。

    判据：一个 STRING token，且它**前面没有任何实义 token**（文件开头也算）。
    所以 `x = "score"` 不算 —— 那前面有 `x` 和 `=`。

    为什么要单独认它：见 修订四。**文档不是产物行为** ——
    docstring 里写不出一个分数来，而 B2 / B5 要抓的是产物**在产生**权重与评分。
    写成字符串当 dict 键仍然照抓（那是产物行为）。
    """
    lines: set[int] = set()
    fresh = True                      # 还没遇到实义 token
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in _TRIVIAL:
            continue
        if tok.type == tokenize.STRING and fresh:
            lines.update(range(tok.start[0], tok.end[0] + 1))
        if tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            fresh = True
        else:
            fresh = False
    return lines


def code_lines(path: Path):
    """(行号, 该行源码去掉注释与 docstring 后的部分)。

    注释起始列由 `tokenize` 给出；docstring 整行去掉。
    """
    src = path.read_text(encoding="utf-8")
    cut: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            cut.setdefault(tok.start[0], tok.start[1])
    doc = _docstring_lines(src)
    for n, line in enumerate(src.splitlines(), 1):
        if n in doc:
            continue
        yield n, (line[:cut[n]] if n in cut else line).rstrip()


def scan(pattern: str) -> list[tuple[str, int, str]]:
    rx = re.compile(pattern, re.I)
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        for line, text in code_lines(path):
            if rx.search(text):
                hits.append((path.relative_to(ROOT).as_posix(), line, text.strip()))
    return hits


def check_no_threshold() -> list[tuple[str, int, str]]:
    """`§C7.2` `§T0.3`：观测点的值不得与任何数比较（不设阈值 / 报警线）。

    这一条**空转过两次**，所以写清楚它跟以前有什么不同：

    1. 最早抓的是百分号字面量 —— 靶子错了，命中的是 `{x:.2%}` 这种**显示格式**，
       而真正的失败模式（拿观测点的值和数比）它根本看不见。
    2. 改成逐行盯比较运算，但**逐行看不见跨行**：

           v = m['unused_ratio']       ← 这行把 v 标成「沾了观测点」
           if v >= 0.2:                ← 阈值在这一行，老版本完全没反应

       「先取出来再比」恰恰是最常见的写法。所以这里**跟局部变量**：
       一行里出现观测点词、且在那个位置做了赋值，就把被赋的名字记下来；
       之后任何拿它比较的行都算命中。

    代价：**会过度报告**。比如 `v` 沾过观测点之后，再 `if v < 5`（其实是个下标边界）
    也会被记一笔。这是刻意的取舍 —— 本检查是否证用的，**漏报是致命的，误报只是吵**。
    """
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        tainted: set[str] = set()
        for n, text in code_lines(path):
            found = _COMPARISON.search(text)
            if found:
                left = text[:found.start()]
                if _METRIC_WORD.search(left) or any(
                    re.search(rf"\b{re.escape(t)}\b", left) for t in tainted
                ):
                    hits.append((rel, n, text.strip()))
            assigned = _ASSIGNED.match(text)
            if assigned and _METRIC_WORD.search(text):
                tainted.add(assigned.group(1))
    return hits


def check_no_new_dependency() -> list[tuple[str, int, str]]:
    """`§T5`：引入新依赖是必须停下来的阻断项。所以这里证明没引。"""
    rx = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)")
    # 本地模块**按整个仓库算**，不只顶层 —— `samples/align.py` 也是本仓库的模块，
    # `from align import ...` 不是引第三方。扫的是 rglob，这里就得跟着 rglob，
    # 否则 B7 会把子目录里的本地导入报成新依赖（2026-09-25 真报了一次假）。
    local = {p.stem for p in ROOT.rglob("*.py")}
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = rx.match(line)
            if not m:
                continue
            top = m.group(1).split(".")[0]
            if top in local or top in sys.stdlib_module_names:
                continue
            hits.append((path.relative_to(ROOT).as_posix(), n, line.strip()))
    return hits


PENDING = "annotation_status: 待需求方标注"


def check_placeholder_not_annotated() -> list[tuple[str, int, str]]:
    """`§C5.5.1` 第 2 条：标注不得由 AI 生成。

    判据：**自己声明了「待需求方标注」的区块，标注区必须是空的。**
    把 `annotation_status` 改成别的值（也就是需求方真标了）之后，这条就不再管它。

    这是防「顺手把 proposed_count 补上，让它看起来像真样本」——
    一旦补上，后面所有一致率都是空转的，而且**看起来很正常**。

    ⚠️ 本检查靠标记触发，所以**标记一旦改名，它会静默空转**。
    2026-09-25 就从 `agent-placeholder` 改成过 `annotation_status`；
    改的时候必须同时确认它还能抓到东西。见 DECLARATION.md §4.2。

    ⚠️⚠️ **2026-09-25 第二次发生，而且这次是主动发生的。**
    需求方授权把标签文件折算成标注，`inputs.md` 里 20 条的
    `annotation_status` 全部离开了 `待需求方标注` ——
    于是**这条检查在那份文件上一条都罩不住了，而它照样打印「过」。**
    这就是上面那句警告说的病，只是这次不是改名改掉的，是**流程正常往前走**走掉的。
    补的是 `check_annotated_samples_name_their_source`（B13）——
    管「有值 → 必须署名」。见 DECLARATION.md §11。
    """
    samples = ROOT / "samples"
    if not samples.is_dir():
        return []
    hits = []
    for path in sorted(samples.glob("*.md")):
        block, start = [], 1
        for n, line in enumerate(
            [*path.read_text(encoding="utf-8").splitlines(), "## __end__"], 1
        ):
            if line.startswith("## "):
                _judge_placeholder(path, start, block, hits)
                block, start = [], n
            block.append((n, line))
    return hits


def _judge_placeholder(path, start, block, hits):
    if not any(PENDING in ln for _, ln in block):
        return
    rel = path.relative_to(ROOT).as_posix()
    for n, ln in block:
        s = ln.strip()
        filled = (s.startswith("proposed_count:") and "null" not in s) or (
            s.startswith("agent_filled:") and "未标注" not in s
        )
        if filled:
            hits.append((rel, n, s))


def _blocks(path):
    """把一个 .md 按 `## ` 切成区块，产出 (起始行号, [(行号, 行)])。"""
    block, start = [], 1
    for n, line in enumerate(
        [*path.read_text(encoding="utf-8").splitlines(), "## __end__"], 1
    ):
        if line.startswith("## "):
            yield start, block
            block, start = [], n
        block.append((n, line))


def check_annotated_samples_name_their_source() -> list[tuple[str, int, str]]:
    """`§C5.5.1` 第 2 条的**另一半** —— B8 只挡了一半。

    B8 管的是「声明待标注 → 标注区必须空」。
    另一半是「**标注区一旦有值 → 必须说清这是谁的**」。
    加它的直接原因：2026-09-25 需求方授权把标签文件折算成标注之后，
    `inputs.md` 里 20 条全部离开了 B8 的状态 ——
    **B8 照样打印「过」，但它在那份文件上一条都罩不住了。**

    这正是 B8 自己的 docstring 警告过的病（靠标记触发，改了标记就静默空转）。
    空转不可怕，**可怕的是它长得和通过一模一样**。所以补上这另一半：
    填了值就必须署名，没署名就响。

    ⚠️ 它挡不住「`agent_filled` 写了但写的是假话」。那测不出来，见 `§C2.4`：
    四项都是**能力**，不是**义务**。
    """
    samples = ROOT / "samples"
    if not samples.is_dir():
        return []
    hits = []
    for path in sorted(samples.glob("*.md")):
        for start, block in _blocks(path):
            if any(PENDING in ln for _, ln in block):
                continue                       # 这一块归 B8 管，不重复报
            named = any(ln.strip().startswith("proposed_count:")
                        and "null" not in ln for _, ln in block)
            if not named:
                continue
            if not any(ln.strip().startswith("agent_filled:")
                       and ln.strip() != "agent_filled:" for _, ln in block):
                hits.append((path.relative_to(ROOT).as_posix(), start,
                             "有 proposed_count 却没有 agent_filled —— 值是谁填的，没说"))
    return hits


# 并发装置的三条红线（`§C11.2` 的判据表）。**只扫这一个文件。**
#
# 为什么不并进 B1 那个全仓正则：这三条**只对并发装置成立**。
# `scaffold.py` 里满是 SQL 才是对的 —— 它本来就是存储层；
# 而并发装置里出现一条 INSERT，那就是「手工补状态」，是剧本不是并发。
# 全仓扫会把它扫成误报，误报到最后就是关掉它 —— 那比不扫更糟。
# 见 DECLARATION.md §4.4。
_SLEEP = re.compile(r"\bsleep\s*\(", re.I)
# **只拦写，不拦读。** `§C11.2` 的原话是「直接**改**数据库 / 手工补状态」——
# 拦的是"我在摆布状态"，不是"我在看状态"。读不出假象来：
# 一条 SELECT 改不了谁先谁后，也就伪造不出一个竞争窗口。
# 第一版我把 SELECT 也拦了，**结果拦住了自己**：`_interleaving()` 要读 event 表
# 才能回答「这两个主体到底有没有真重叠」——那是本步**最该测**的一件事。
# 一条严到必须绕过自己的检查，最后的下场就是被关掉。见 DECLARATION §4.4。
_SQL_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|REPLACE\s+INTO)\b",
                        re.I)
_COORD = re.compile(r"\b(threading|multiprocessing|asyncio|Barrier|Semaphore)\b")


def check_apparatus_is_not_a_script(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """`§C11.2` 判据表：禁止注入延迟、禁止直接改数据库、禁止协调两个主体。

    本步**唯一**允许的写法是：起两个真进程，让它们各自调产品 API，
    然后看发生了什么。任何一处 `sleep` / 写库 / 同步原语，都是我在替它们决定
    谁先谁后 —— 那样打出来的「问题」是我安排的，不是系统暴露的。

    **只拦写，不拦读** —— 理由写在 `_SQL_WRITE` 上面。

    判据是**文件里有没有这些东西**，不是「跑起来像不像并发」。
    后者测不出来；前者一查就知道。

    `target` 可覆盖，只为了 `test_checks.py` 能拿临时探针验这条不是空转 ——
    跟 B8 一个道理：**只扫一个固定文件，跟扫一个改名就消失的标记，是同一种脆。**
    """
    target = target or (ROOT / "concurrent.py")
    if not target.is_file():
        return []                      # 还没写并发装置 —— 这条暂不适用，不是「过」
    hits = []
    for n, line in code_lines(target):
        for rx, why in ((_SLEEP, "注入延迟"), (_SQL_WRITE, "直接改数据库"),
                        (_COORD, "在装置内部做协调")):
            if rx.search(line):
                hits.append((target.name, n, f"{line.strip()}   ← {why}"))
    return hits


def check_allocator_stays_an_allocator() -> list[tuple[str, int, str]]:
    """`§T4.1` B 类自证（`§T2` 第 12 步）：**发号器不许携带任何判断。**

    这一步选的是「让 SQLite 原子发号」—— 一次**锁策略**层面的选择，
    所以按 B 类规矩，得落成一条可执行的否证检查，证明它不触碰不变量。

    它可能触碰的是 `§C9` **#5（Popularity = Evidence）** 与
    **#7（Evidence 自带固定 truth score）**：这两个不变量管的是
    「不许把某个**量**读成判断」。而 `seq.n` 恰好就是一个量。

    它现在安全，是因为 **它出不了 `scaffold.py`** —— 号只被用来拼成一个字符串
    （`claim-0001`），拼完这个数就没了，没有任何视图、计数、排序读过它。
    哪一天有人把 `seq.n` 拎出来放进 `view()` 或 `tally()`，
    它就从「发号器」变成了「一个可排序、可比大小的数」—— 那正是 #5 / #7 要防的路。

    判据：**`seq` 这张表只在一个文件里被提到**。
    这是它的**全部**约束，也正是我敢说它不违反 #5 / #7 的理由。
    """
    hits = []
    for path in sorted(ROOT.glob("*.py")):
        if path.name in EXEMPT:
            continue
        for n, line in code_lines(path):
            if re.search(r"\b(FROM|INTO|UPDATE|JOIN|TABLE)\s+seq\b", line, re.I):
                if path.name != "scaffold.py":
                    hits.append((path.name, n, line.strip()))
    return hits


# 缺口②：「命题的节点类型不许被写死」。**只扫这一个文件。**
#
# 为什么只扫 `confirm.py`：`type_="Topic"`、`type_="Counterargument"` 这类字面量
# 在 `debate.py` 里全是**对的** —— 建 Mechanism 就是建 Mechanism。
# 会出错的是**命题**的类型。
#
# ⚠️ 2026-09-25 需求方把缺口② 从第 1 档改成第 2 档（默认生效 + 可推翻 + 抽检 +
# 计改判率）之后，**这条检查的理由变了，得说清楚，不能让旧理由留着糊过去**：
#
#   改之前它挡的是「引擎自决」—— 定完才写，所以写死一个类型 = 替人做了选择。
#   改之后**默认本来就是 Claim**，写死 `type_="Claim"` 和走候选表
#   在「默认那一下」产出完全相同的字节。**那半个理由没有了。**
#
#   它现在挡的是第 2 档的第三条「**可推翻**」：
#   走 `chosen.get(...)` 时，`resolve_types()` 里人给的值能到达 artifact；
#   写死 `type_="Claim"` 时，用户改过的那一笔会**只进事件表、不进结构** ——
#   改判记录下来了，改判没生效。那比第 1 档更糟：它看起来像是尊重了判断。
#
# 这个检查**有洞**，说清楚：`t = "Claim"; add_artifact(type_=t, ...)` 绕得过去。
# 洞不补 —— 补它要靠数据流分析，成本远超收益。它挡的是**改回去那个动作本身**，
# 不是「有人存心绕」。见 DECLARATION §10.3。
_HARDCODED_PROPOSITION_TYPE = re.compile(
    r"""type_\s*=\s*["'](?:Claim|Evidence)["']""")


def check_proposition_type_is_not_hardcoded(
    target: Path | None = None,
) -> list[tuple[str, int, str]]:
    """缺口②：命题的节点类型不许写死 —— 写死就等于**推翻不了**。

    改坏它的方式很具体：把 `confirm()` 里那句
    `type_=chosen.get(x["key"], "Claim")` 换回 `type_="Claim"`。
    换回去之后，`resolve_types()` 里人给的 `Evidence` 到不了 artifact ——
    事件表里记着「用户改判了」，结构里还是 `Claim`。
    这个检查就盯那一行。

    它**只**保证这一点。第二档里「默认生效」「计改判率」两条不归它管
    （前者本来就是行为，后者由 `types_defaulted` 事件保证，见下面的注释）。

    `target` 可覆盖，为的是 `test_checks.py` 能注入探针证明它不空转 ——
    跟 B8 / B9 一个道理。
    """
    target = target or (ROOT / "confirm.py")
    if not target.is_file():
        return []                      # 还没写确认链 —— 这条暂不适用，不是「过」
    return [(target.name, n, line.strip())
            for n, line in code_lines(target)
            if _HARDCODED_PROPOSITION_TYPE.search(line)]


def all_checks():
    """(编号, 说明, 条款, 返回命中的函数) —— **唯一的登记表**。

    存在理由：`main()` 和 `test_checks.py` 都从这里取。
    以前 B6 / B7 / B8 是在 `main()` 里手工追加的，于是
    `test_all_checks_are_non_vacuous` 那条总账**盖不到它们** ——
    也就是说，将来它们变成空转，总账仍然是绿的。
    那正是这个文件要防的病。现在加一条检查只要改这一处。
    """
    for code, what, pattern, clause in CHECKS:
        yield code, what, clause, partial(scan, pattern)
    yield "B6", "观测点只记不算——不拿它的值与任何数比较", "§C7.2 §T0.3", check_no_threshold
    yield "B7", "第三方依赖为零", "§T4.3 §T5", check_no_new_dependency
    yield "B8", "声明待标注的样本，标注区为空", "§C5.5.1", check_placeholder_not_annotated
    yield ("B9", "并发装置不注入延迟 / 不直接改库 / 不内部协调", "§C11.2",
           check_apparatus_is_not_a_script)
    yield ("B10", "发号器不携带判断（号出不了 scaffold.py）", "§C9 #5 #7",
           check_allocator_stays_an_allocator)
    yield ("B12", "命题类型不许写死 —— 写死就推翻不了（confirm.py 无类型字面量）",
           "§T0.3 §C2.5 缺口②", check_proposition_type_is_not_hardcoded)
    yield ("B13", "标注区有值的样本，必须说清值是谁填的（B8 的另一半）", "§C5.5.1",
           check_annotated_samples_name_their_source)


def main() -> int:
    failed, codes = [], []
    for code, what, clause, run in all_checks():
        codes.append(code)
        hits = run()
        print(f"[{code}] {what:<34} {clause:<22} "
              f"{'过' if not hits else f'命中 {len(hits)}'}")
        for path, line, text in hits:
            print(f"       {path}:{line}  {text}")
        if hits:
            failed.append(code)

    print()
    if failed:
        print(f"否证检查未通过：{', '.join(failed)} —— 《选型声明》不成立，停下提问。")
        return 1
    # 以前这里印的是 `{codes[0]}–{codes[-1]}`。加了 B11 之后那句话就**错了** ——
    # B11 不在末尾，却会被那句话盖进「B1–B10」这个区间里。
    # 一句话说得比它知道的多，就是本仓库一直在防的那个病。改成照实报集合。
    print(f"否证检查全部通过：共 {len(codes)} 条，{', '.join(sorted(codes))} 无命中。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
