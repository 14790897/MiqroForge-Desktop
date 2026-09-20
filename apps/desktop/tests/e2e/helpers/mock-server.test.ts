/**
 * #1118 第九轮：mock 启动行的**分块**匹配。
 *
 * Node 的 stdout pipe 会把一次 write 切成任意多个 'data' 事件（尤其 CI 上管道
 * 背压/缓冲不同时），旧实现在**单个 chunk** 上跑正则：mock 打印
 * `Mock hang server on http://127.0.0.1:<port>/v1` 时若恰好在 URL 中间断开，
 * Ready 行就永远匹配不上——循环空转到 30s 超时，报「startup line not seen」，
 * 与「mock 起得慢 / spawn 失败」完全同症状。这里把拼接后的行为钉死。
 */
import { describe, expect, it } from 'vitest';
import { matchReadyUrl } from './mock-server';

const READY_LINE = 'Mock hang server on http://127.0.0.1:23456/v1\n';

describe('matchReadyUrl', () => {
  it('完整一行 → 解析出 ready URL', () => {
    expect(matchReadyUrl(READY_LINE)).toBe('http://127.0.0.1:23456/v1');
  });

  it('一行被切成两段、累计后拼接 → 仍能解析（单 chunk 匹配会漏的形状）', () => {
    const head = READY_LINE.slice(0, 30); // 'Mock hang server on http://127.0.'
    const tail = READY_LINE.slice(30); // '0.1:23456/v1\n'
    // 单看任意一段都匹配不到 —— 这正是旧实现的失败点。
    expect(matchReadyUrl(head)).toBeNull();
    expect(matchReadyUrl(tail)).toBeNull();
    // 累计后命中。
    expect(matchReadyUrl(head + tail)).toBe('http://127.0.0.1:23456/v1');
  });

  it('逐字节喂进来（最碎的分块）也能在第 N 段命中', () => {
    let acc = '';
    let hit: string | null = null;
    for (const ch of READY_LINE) {
      acc += ch;
      hit = matchReadyUrl(acc);
      if (hit) break;
    }
    expect(hit).toBe('http://127.0.0.1:23456/v1');
  });

  it('前面还有别的输出（多行）时照常解析', () => {
    const acc = `[mock-python] interpreter=python3\n${READY_LINE}listening…\n`;
    expect(matchReadyUrl(acc)).toBe('http://127.0.0.1:23456/v1');
  });

  it('还没打出 ready 行 → null（不能凭端口/半截 URL 误判）', () => {
    expect(matchReadyUrl('')).toBeNull();
    expect(matchReadyUrl('Mock hang server on http://127.0.0.1:')).toBeNull();
    expect(matchReadyUrl('Mock hang server on http://127.0.0.1:23456/v1')).not.toBeNull();
    // 非 127.0.0.1（如绑到 lan 地址）不算 ready —— 断言只认这一种形状。
    expect(matchReadyUrl('http://0.0.0.0:23456/v1')).toBeNull();
  });
});
