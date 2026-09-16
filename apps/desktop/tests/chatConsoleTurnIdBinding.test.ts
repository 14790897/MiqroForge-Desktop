import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #1071 review P1（2026-09-16）回归锁。
 *
 * 背景：live 落盘路径（reveal 完成更新/新建 assistant bubble、工具行 toolMsg）的
 * `turnId` 曾经取自 `activeTurnIdRef.current`。该 ref 是**跨 invocation 共享**的
 * 组件级 ref，别的 turn 的 `turn_started` 会改写它，于是并发/交叠发送时本消息会被
 * 绑到错误的 turn（链卡、中断卡归属全跟着错）。
 *
 * 不变量：live 路径的 `turnId` 只能取 **invocation-local** 的 `myTurnId`
 * （在 `handleSend` 闭包内声明、由本 invocation 收到的 `turn_started` 赋值）。
 *
 * 本文件是**源码文本锁**：它 grep ChatConsole.tsx 的源码而不是跑运行时行为，
 * 因为该竞态需要两个并发 invocation 的事件交错才能触发，单测难以稳定复现。
 *
 * ⚠️ 若未来重构改动了变量名（`myTurnId` / `activeTurnIdRef`），请同步更新本锁，
 * 否则这里会以「源码模式不匹配」的形式失败——那是锁需要维护，不是代码有 bug。
 */

const CANDIDATES = [
  // vitest 通常以 apps/desktop 为 cwd 运行
  resolve(process.cwd(), 'src/renderer/features/chat/ChatConsole.tsx'),
  // 兜底：从仓库根运行
  resolve(process.cwd(), 'apps/desktop/src/renderer/features/chat/ChatConsole.tsx'),
];

const sourcePath = CANDIDATES.find((candidate) => existsSync(candidate));

if (!sourcePath) {
  throw new Error(
    `chatConsoleTurnIdBinding: 找不到 ChatConsole.tsx，已尝试：\n${CANDIDATES.join('\n')}`
  );
}

const source = readFileSync(sourcePath, 'utf8');

const countMatches = (pattern: RegExp) => source.match(pattern)?.length ?? 0;

describe('ChatConsole live turnId binding (#1071 review P1)', () => {
  it('never binds live 消息的 turnId 到跨 invocation 共享的 activeTurnIdRef', () => {
    const offenders = source.match(/turnId:\s*activeTurnIdRef\.current/g) ?? [];

    expect(offenders, `发现 ${offenders.length} 处 turnId 仍取自 activeTurnIdRef.current`).toEqual(
      []
    );
  });

  it('binds 至少 4 处 turnId 到 invocation-local 的 myTurnId（reveal×3 + 工具行×1）', () => {
    const bindings = countMatches(/turnId:\s*myTurnId\s*\?\?/g);

    expect(bindings).toBeGreaterThanOrEqual(4);
  });

  it('myTurnId 仍是 handleSend 闭包内的 invocation-local 变量', () => {
    // 声明必须是 let（后续由 turn_started 赋值），且不是组件级 useRef。
    expect(source).toMatch(/let\s+myTurnId\s*:\s*string\s*\|\s*null\s*=\s*null/);
    expect(source).not.toMatch(/myTurnId\s*=\s*useRef/);
  });
});
