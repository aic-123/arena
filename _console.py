"""让命令行输出不依赖运行环境的代码页。

**为什么需要它**：这个仓库的所有入口都用中文打印
（`过` / `命中` / 检查名 / 确认提示）。Windows 上 `python` 的
`sys.stdout.encoding` 默认是 **cp1252**，不是 utf-8 —— 打印第一个中文字符
就 `UnicodeEncodeError`，进程退出码 1。

危险的不是崩，是**它崩的样子**：

- `checks.py` 退出 1 看起来**和「有一条否证检查命中了」一模一样**。
  2026-09-26 实测：CI 的 windows 两条腿挂在 `checks.py`，
  报的是「Process completed with exit code 1」，像是 B 类声明不成立。
  真正的原因只是控制台编不出中文 ——
  **一句工具自己的事故被读成了产物的结论。**
- `cli.py` 退出 1 看起来像**用户输入有问题**。其实用户什么都没做错。

**本地一直看不出来**，因为开发机的环境里恰好有 `PYTHONIOENCODING=utf-8`
和 `PYTHONUTF8=1`（宿主注入）。所以这又是一次「**本地全过 ≠ 换台干净机器也过**」——
和 `samples/align.py` 那次同一个病：上次喂它的是「仓库外面恰好存在的文件」，
这次喂它的是「恰好设了的环境变量」。**两次都不是环境差异，是依赖了不该依赖的东西。**

用 `errors="replace"` 兜底：**宁可打出一个问号，也不要让打印本身变成失败**。
一个缺失的汉字远没有一个假的退出码危险 —— 退出码是会被别人读成结论的。
"""

from __future__ import annotations

import sys


def force_utf8() -> None:
    """把 stdout / stderr 重设为 utf-8。改不了就沉默降级。

    改不了是正常情况：输出可能被重定向成别的东西（测试里抓管道、
    IDE 的控制台包装），那种对象没有 `reconfigure`。
    收尾不该因为「这台机器不让我改」而抛 —— 那会把真正的异常盖掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
