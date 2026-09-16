import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #1071 评审 P2-a（2026-09-16）回归锁。
 *
 * 背景：HermesConfirmBar 用 window keydown **capture** 监听 Esc → deny。capture 阶段
 * 早于 Radix 的下拉菜单处理，所以「更多选项」菜单打开时按 Esc，会先把确认卡拒掉、
 * 菜单还没关——用户想关菜单，结果卡没了。
 *
 * 不变量：Esc 分支必须先让位给打开中的下拉（`menuOpenRef.current` → return），
 * 且该让位判断要早于 `preventDefault()` / 拒绝提交（`resolveOnce('deny')`）。
 * 另外菜单必须是**受控**的（`open={menuOpen}` + `onOpenChange`），否则 ref 拿不到真值。
 *
 * #1071 G7 P1（外部评审）后：Esc 的拒绝也改走统一的提交闸门 `resolveOnce`（失败可
 * 重试），不再是裸 `onResolve('deny')`——本锁随之改为锁定 `resolveOnce('deny')`，
 * 并**反向断言**这里没有绕过闸门直呼 onResolve。
 *
 * 本文件是**源码文本锁**：该行为依赖 Radix 菜单真实开合 + window capture 时序，
 * jsdom 下无法稳定复现（本仓库组件测试只有 react-dom/server 的静态渲染）。锁的是
 * 源码结构而非运行时行为——与前例 tests/chatConsoleTurnIdBinding.test.ts 同模式。
 *
 * ⚠️ 若未来重构改动了状态变量名（`menuOpen` / `menuOpenRef`），请同步更新本锁，
 * 否则这里会以「源码模式不匹配」的形式失败——那是锁需要维护，不是代码有 bug。
 */

const CANDIDATES = [
  // vitest 通常以 apps/desktop 为 cwd 运行
  resolve(process.cwd(), 'src/renderer/features/chat/components/HermesConfirmBar.tsx'),
  // 兜底：从仓库根运行
  resolve(process.cwd(), 'apps/desktop/src/renderer/features/chat/components/HermesConfirmBar.tsx'),
];

const sourcePath = CANDIDATES.find((candidate) => existsSync(candidate));

if (!sourcePath) {
  throw new Error(
    `hermesConfirmBarEscapeYield: 找不到 HermesConfirmBar.tsx，已尝试：\n${CANDIDATES.join('\n')}`
  );
}

const source = readFileSync(sourcePath, 'utf8');

/** 截取 Esc 分支：从 `} else if (event.key === 'Escape') {` 到 `}` 收尾。 */
function escapeBranch(): string {
  const start = source.indexOf("event.key === 'Escape'");
  expect(start, '找不到 Esc 分支').toBeGreaterThan(-1);
  const end = source.indexOf('}', start);
  return source.slice(start, end);
}

describe('HermesConfirmBar：Esc 让位给打开中的下拉（#1071 P2-a）', () => {
  it('下拉 Root 受控：open={menuOpen} + onOpenChange 回写状态', () => {
    expect(source).toMatch(
      /<DropdownMenu\.Root\s+open=\{menuOpen\}\s+onOpenChange=\{setMenuOpen\}/
    );
    expect(source).toMatch(/const \[menuOpen, setMenuOpen\] = useState\(false\)/);
  });

  it('Esc 分支先让位再拒绝：menuOpenRef 判断早于 preventDefault / resolveOnce', () => {
    const branch = escapeBranch();
    const yieldIdx = branch.indexOf('menuOpenRef.current');
    const preventIdx = branch.indexOf('preventDefault');
    const denyIdx = branch.indexOf("resolveOnce('deny')");
    expect(yieldIdx, 'Esc 分支缺少「下拉打开时让位」判断').toBeGreaterThan(-1);
    expect(preventIdx).toBeGreaterThan(-1);
    expect(denyIdx).toBeGreaterThan(-1);
    expect(yieldIdx, '让位判断必须在 preventDefault 之前').toBeLessThan(preventIdx);
    expect(yieldIdx, '让位判断必须在拒绝提交之前').toBeLessThan(denyIdx);
  });

  it('让位用 ref 读最新值（effect 不因 menuOpen 重建闭包）', () => {
    expect(source).toMatch(/const menuOpenRef = useRef\(menuOpen\)/);
    expect(source).toMatch(/menuOpenRef\.current = menuOpen/);
  });

  it('未打开菜单时 Esc 仍然拒绝（没有把功能一起让掉），且必须走统一提交闸门', () => {
    const branch = escapeBranch();
    expect(branch).toContain('event.__miqiResolved = true');
    expect(branch).toContain("resolveOnce('deny')");
    // G7 P1：Esc 与主按钮共用同一把锁——这里不能再有绕过闸门的裸调用，
    // 否则失败后按钮解锁、Esc 那条路仍然会重复提交同一张卡。
    expect(branch).not.toContain('onResolve(');
    // 输入框让位不受影响
    expect(branch).toContain('editing');
  });
});
