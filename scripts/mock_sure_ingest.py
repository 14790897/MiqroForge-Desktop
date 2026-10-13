"""SURE 事件桥接 E2E:mock 'sure' 二进制——把 `sure hook ingest` 的 stdin
事件原样追加到捕获文件(每行一条 JSON)。

为什么捕获路径走 argv 而不是环境变量:桥接 spawn ingest 时用的是白名单
env(build_sure_env,不透传任意变量),所以包装脚本把捕获文件路径固定为
本脚本的第一个参数:

    <python> mock_sure_ingest.py <捕获文件> %*

注意:捕获文件路径会被写进 .cmd 包装脚本内容——**必须位于纯 ASCII 路径**
(cmd.exe 以系统 ANSI 代码页读取 .cmd 文件字节,中文系统的 GBK 会把
UTF-8 中文路径读坏,连路径分隔符一起吞掉;桥接 E2E 因此把临时目录建在
仓库内——与 pytest conftest 把 basetemp 钉在仓库内同款考虑)。

- `--version`:回一行静态版本(应用的健康探测);
- `hook ingest …`:读 stdin 全量,追加一行,退出 0(桥接看起来即写入成功);
- 其余命令(如应用探测到 SURE_BIN 后尝试连接的 `sure mcp serve`):立即
  退出 0,不读 stdin——MCP 握手立刻 EOF 失败并被跳过,不拖慢会话启动
  (与 mock_sure_cli.py 的 fast-exit 行为同款,acceptance-page E2E 已在
  三平台 CI 验证过这条路径)。
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 2:
        return
    capture = sys.argv[1]
    args = sys.argv[2:]
    if "--version" in args:
        print("sure 9.9.9-mock")
        return
    if args[:2] == ["hook", "ingest"]:
        # 字节透传(与真实 sure.exe 的 stdin 语义一致):文本模式 sys.stdin.read()
        # 会按 Windows ANSI 代码页解码 UTF-8 载荷,中文路径变 lone surrogate,
        # 写出时 UnicodeEncodeError(本机中文用户名实测踩中)。
        data = sys.stdin.buffer.read()
        with open(capture, "ab") as fh:
            fh.write(data + b"\n")


if __name__ == "__main__":
    main()
