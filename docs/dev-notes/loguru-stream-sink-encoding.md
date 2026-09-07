---
name: loguru-stream-sink-encoding
description: loguru add() 对 stream sink 拒绝 encoding 参数（只有路径 sink 接受），正确做法是 reconfigure + PYTHONIOENCODING
type: reference
---

loguru `logger.add()` 的 `encoding` 关键字**只对路径 sink（str/PathLike）有效**；传入 `sys.stderr` 这类 stream sink 会直接 `TypeError: add() got an unexpected keyword argument 'encoding'`（0.7.3 源码：stream 分支读 `sink.encoding` 属性，kwargs 未消费即报错）。第一次修复中文编码崩溃时踩过这个坑，导致 bridge 启动即崩。

**Why:** loguru 对已打开的流无法改编码，只能读流自身的 `encoding` 属性。

**How to apply:** 修中文写 stderr 崩溃用两层：① `sys.stderr.reconfigure(encoding='utf-8')`（改流对象，loguru 随之读到 utf-8）；② 在 Electron spawn 的 env 里加 `PYTHONIOENCODING: 'utf-8'`（解释器启动即生效，且被子进程继承）。参考 [dirty-api-key-httpx-crash](dirty-api-key-httpx-crash.md)。
