"""Windows loopback-safe socketpair.

Background (#packaged-app-hang):
某些 Windows 环境存在“按二进制身份”选择性丢弃 127.0.0.1 回环连接的网络过滤
（安全软件 WFP 驱动/残留规则：签名/知名程序放行，未签名程序（PyInstaller
打包的 exe、uv 管理的 Python 等）的回环 SYN 被静默丢弃，外网与 LAN 路径不受影响）。

Python 在 Windows 上没有原生 socketpair：Lib/socket.py 的 _fallback_socketpair
通过 127.0.0.1 TCP + 非阻塞 connect + 阻塞 accept 模拟。asyncio 的
ProactorEventLoop / SelectorEventLoop 都在创建事件循环时调用它
（_make_self_pipe）。回环被拦时 accept() 永久阻塞 —— bridge 的 ready 信号
发不出，桌面端永远卡“启动中”。

本模块在安装时探测回环健康度：
- 健康：完全保持原生行为（零开销、零行为差异）
- 不健康：把 socket.socketpair 替换为走 LAN IP 的实现（bind 0.0.0.0、
  connect 本机局域网 IPv4），绕过被拦的回环路径

探测本身带 2 秒超时守护：探测线程为 daemon，最坏情况泄漏一个阻塞线程
（仅发生在回环已损坏的机器上，可接受）。
"""

from __future__ import annotations

import socket
import sys
import threading

_PROBE_TIMEOUT_S = 2.0

# Non-broadcast targets only: on a 192.168.0.0/16 adapter Windows treats
# *.255.255 as a directed broadcast, and UDP connect() to it fails with
# WSAEACCES unless SO_BROADCAST is set — which would silently disable the
# LAN fallback on exactly the machines it exists for. Each candidate just
# makes the kernel pick the matching route; no packet is ever sent.
_LOOPBACK_FREE_TARGETS = (("192.168.255.254", 1), ("10.255.255.254", 1), ("8.8.8.8", 80))

_state: dict[str, object] = {"installed": False, "checked": False, "healthy": True}
_orig_socketpair = None


def _lan_ipv4() -> str | None:
    """Get the machine's LAN IPv4 without sending any packet (UDP connect trick)."""
    for target in _LOOPBACK_FREE_TARGETS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(target)
            ip = s.getsockname()[0]
        except OSError:
            continue
        finally:
            s.close()
        if ip and ip != "127.0.0.1":
            return ip
    return None


def _lan_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    """socketpair-compatible pair built via the LAN address instead of loopback."""
    if family != socket.AF_INET:
        raise ValueError(f"lan socketpair supports AF_INET only, got {family!r}")
    if type != socket.SOCK_STREAM:
        raise ValueError("lan socketpair supports SOCK_STREAM only")
    if proto != 0:
        raise ValueError("lan socketpair supports proto 0 only")

    lan_ip = _lan_ipv4()
    if not lan_ip:
        raise OSError("no LAN IPv4 address available for lan socketpair")

    lsock = socket.socket(family, type, proto)
    try:
        # Bind only the LAN address: this pair is a private in-process channel
        # that must never accept remote connections, so it must not listen on
        # all interfaces (CodeQL: socket bind to all interfaces).
        lsock.bind((lan_ip, 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]

        csock = socket.socket(family, type, proto)
        try:
            csock.settimeout(10)
            csock.connect((lan_ip, port))
            csock.settimeout(None)
            ssock, _ = lsock.accept()
            ssock.settimeout(None)
        except Exception:
            csock.close()
            raise
    finally:
        lsock.close()
    return ssock, csock


def _probe_loopback() -> bool:
    """Run one native socketpair in a daemon thread with a timeout guard."""
    result: dict[str, bool] = {}

    def _run() -> None:
        try:
            a, b = socket.socketpair()
            a.close()
            b.close()
            result["ok"] = True
        except OSError:
            result["ok"] = False

    t = threading.Thread(target=_run, name="loopback-probe", daemon=True)
    t.start()
    t.join(_PROBE_TIMEOUT_S)
    return bool(result.get("ok"))


def _guarded_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    """Drop-in socket.socketpair replacement that falls back to the LAN path."""
    if not _state["checked"]:
        _state["checked"] = True
        _state["healthy"] = _probe_loopback()
    if _state["healthy"] or family != socket.AF_INET:
        assert _orig_socketpair is not None
        return _orig_socketpair(family, type, proto)
    return _lan_socketpair(family, type, proto)


def install_loopback_safe_socketpair() -> bool:
    """Install the guarded socketpair. Returns True when the LAN fallback is active.

    No-op on non-Windows platforms and when no LAN IPv4 exists.
    """
    global _orig_socketpair
    if _state["installed"]:
        return bool(not _state["healthy"])
    if sys.platform != "win32":
        _state["installed"] = True
        return False
    if not _lan_ipv4():
        # No LAN address to fall back to — keep native behavior.
        _state["installed"] = True
        return False

    _orig_socketpair = socket.socketpair
    socket.socketpair = _guarded_socketpair  # type: ignore[assignment]
    _state["installed"] = True
    return True


def loopback_is_healthy() -> bool | None:
    """None until first socketpair call; then True/False after the probe ran."""
    if not _state["checked"]:
        return None
    return bool(_state["healthy"])  # type: ignore[return-value]
