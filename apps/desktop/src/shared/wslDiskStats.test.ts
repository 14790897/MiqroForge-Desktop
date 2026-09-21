import { describe, expect, it } from 'vitest';
import { makeWslDiskStats } from './wslDiskStats';

/** 浮点比较：0.05 容差足够区分「四舍五入的尾差」与「真实对不上账」 */
const close = (a: number, b: number) => Math.abs(a - b) < 0.05;

describe('makeWslDiskStats', () => {
  // ── #1157 实测值（AIShadowSandbox, ext4 默认 5% 保留块）─────────────
  // df --output=size,used,avail --block-size=1M / → 1031019 2856 975719
  // 1006.9 − 2.8 = 1004.1 ≠ 952.9，差额 51.2GB 就是 ext4 的保留块
  it('把 df 的「总量−已用≠可用」差额算成系统保留', () => {
    const d = makeWslDiskStats(1031019, 2856, 975719);
    expect(d.total_gb).toBe(1006.9);
    expect(d.used_gb).toBe(2.8);
    expect(d.free_gb).toBe(952.9);
    expect(d.reserved_gb).toBe(51.2);
    expect(d.used_pct).toBe(0);
  });

  it('三项加保留严格等于总量（用户能直接对账）', () => {
    const d = makeWslDiskStats(1031019, 2856, 975719);
    expect(close(d.used_gb + d.free_gb + d.reserved_gb, d.total_gb)).toBe(true);
  });

  // ── 无保留块的文件系统（xfs/btrfs/vfat）────────────────────────────
  it('avail = total − used 时保留为 0', () => {
    const d = makeWslDiskStats(1048576, 524288, 524288);
    expect(d.total_gb).toBe(1024);
    expect(d.used_gb).toBe(512);
    expect(d.free_gb).toBe(512);
    expect(d.reserved_gb).toBe(0);
    expect(d.used_pct).toBe(50);
  });

  it('四舍五入的尾差不会让保留变成负数', () => {
    // 1.04GB 用掉 0.51GB，可用 0.52GB（各自 round1 后相加会比总量多 0.0x）
    const d = makeWslDiskStats(1066, 528, 534);
    expect(d.reserved_gb).toBeGreaterThanOrEqual(0);
  });

  // ── 边界 ──────────────────────────────────────────────────────────
  it('采集失败（total=0）时全 0，不产生 NaN', () => {
    const d = makeWslDiskStats(0, 0, 0);
    expect(d).toEqual({ total_gb: 0, used_gb: 0, free_gb: 0, reserved_gb: 0, used_pct: 0 });
  });

  it('盘写满时 used_pct 到 100 且保留不吞掉可用', () => {
    const d = makeWslDiskStats(1031019, 978575, 0);
    expect(d.free_gb).toBe(0);
    expect(d.reserved_gb).toBe(51.3);
    expect(d.used_pct).toBe(95);
  });
});
