/** WSL 磁盘统计的口径换算。
 *
 *  df 的 total/used/avail 不是同一口径：ext4 默认把 5% 的块留给 root，
 *  avail 已经扣掉这部分，所以 `total − used ≠ avail`（1007GB 的盘实测差约
 *  50GB）。放在 shared 是因为主进程负责换算、渲染层负责展示，两边必须对
 *  「磁盘可用为什么不等于总量减已用」给出同一个答案（#1157）。 */

export interface WslDiskStats {
  total_gb: number;
  used_gb: number;
  /** df 的 avail，即普通用户真正能写入的空间（已扣除 reserved_gb） */
  free_gb: number;
  /** 文件系统为 root 预留、普通用户不可用的空间；无预留的文件系统为 0 */
  reserved_gb: number;
  used_pct: number; // 0-100
}

const round1 = (v: number) => Math.round(v * 10) / 10;

/** 入参为 `df --output=size,used,avail --block-size=1M` 输出的三个 MB 值 */
export function makeWslDiskStats(totalMb: number, usedMb: number, availMb: number): WslDiskStats {
  const total_gb = round1(totalMb / 1024);
  const used_gb = round1(usedMb / 1024);
  const free_gb = round1(availMb / 1024);
  // 由展示值反推而不是从 MB 单独换算：否则三处四舍五入会重新引入 0.1GB 级的
  // 「对不上账」，「总量 = 已用 + 可用 + 系统保留」必须在屏幕上严格成立。
  const reserved_gb = Math.max(0, round1(total_gb - used_gb - free_gb));
  const used_pct = totalMb > 0 ? Math.round((usedMb / totalMb) * 100) : 0;
  return { total_gb, used_gb, free_gb, reserved_gb, used_pct };
}
