"""等 Arena 服务起来，并确认**起来的是它**。给 `打开界面.bat` 用。

     python wait_port.py 8765      # 确认是我们 → exit 0；否则 exit 1

这个文件是**界面的一部分，不是仓库的入口**。

⚠️ 为什么不是「端口通了就算」（第一版就是这么写的，然后踩了）：

端口上有人不等于端口上是**我们的**服务。实测踩到过：上一轮留下的
`serve.py` 还在 8765 上监听，新的那个绑不上就静默退出，而这里只探到
「端口通」→ 报告成功 → 浏览器打开的是**旧版本**的页面，
而旧版本没有写入表单 —— 用户看到的就是「功能没了」。

所以这里要验两件事：
1. 端口通不通
2. 响应的 `Server:` 头是不是 `Arena/`（`Handler.server_version`）

第 2 条把「端口被别的程序占了」和「被旧的自己占了」一起挡住了。

⚠️ 关于**等多久**（第一版这里算错过，注释写「最多约 10 秒」，实际是 70 秒）：
预算由 `TIMEOUT × (TRIES + 1)` 决定，**不是** `TIMEOUT × TRIES` ——
因为 `connect()` 要等**一个**完整的 timeout 才会失败，循环体里那次
`time.sleep()` 也会照走。第一版是 `1.5 × 41 ≈ 70 秒`，端口没人监听时实测
**70.89 秒**（对得上）。

这一版把它压到 `0.6 × 11 ≈ 6.6 秒` 量级：服务没起来时用户**几秒内**就看到
「起不来」，而服务正常时第一次尝试通常就命中、瞬间返回。中间的重试
是留给「服务刚 `fork`、端口还没绑上」那一小段窗口。
"""
import http.client
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
host = "127.0.0.1"

TIMEOUT = 0.6  # 单次连接超时
TRIES = 10     # 重试次数；总预算 ≈ TIMEOUT × (TRIES + 1) ≈ 6.6 秒

for _ in range(TRIES):
    try:
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.request("HEAD", "/")
        resp = conn.getresponse()
        server = resp.getheader("Server", "")
        conn.close()
        if server.startswith("Arena/"):
            sys.exit(0)
        # 端口通，但那边不是我们的东西 —— 直接失败，别等。
        sys.exit(2)
    except OSError:
        pass
    import time

    time.sleep(0.6)

sys.exit(1)
