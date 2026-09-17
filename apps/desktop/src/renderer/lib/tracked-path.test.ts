import { describe, expect, it } from 'vitest';

import { sameTrackedFile } from './tracked-path';

const BOUND = 'C:/Users/Guo/Desktop/test';

describe('sameTrackedFile (#1096)', () => {
  it('collapses a workspace-relative key against the absolute path of the same file', () => {
    // 用户那台机器上踩的就是这个形态：账本存相对 key，工具消息报绝对路径。
    expect(sameTrackedFile('冷笑话/冷笑话合集.pdf', `${BOUND}/冷笑话/冷笑话合集.pdf`, BOUND)).toBe(
      true
    );
    expect(sameTrackedFile('sub/a.pdf', 'C:\\bound\\sub\\a.pdf', 'C:\\bound')).toBe(true);
  });

  it('treats identical paths as the same file, with or without a root', () => {
    expect(sameTrackedFile('a/b.pdf', 'a/b.pdf')).toBe(true);
    expect(sameTrackedFile('C:/ws/a.pdf', 'C:/ws/a.pdf', BOUND)).toBe(true);
  });

  it('does NOT merge two relative paths that merely share a tail', () => {
    // 不同深度的同名文件是两个文件（评审明确要求 count === 2）。
    expect(sameTrackedFile('sub/a.pdf', 'other/sub/a.pdf', BOUND)).toBe(false);
  });

  it('does NOT merge a relative key with an absolute path under a DIFFERENT root', () => {
    // 关键反例：后缀相同，但绝对路径不在本会话工作区下 —— 必须 false。
    expect(sameTrackedFile('sub/a.pdf', 'C:/other-root/sub/a.pdf', BOUND)).toBe(false);
  });

  it('does NOT merge two absolute paths that merely share a tail', () => {
    expect(sameTrackedFile('C:/ws/sub/a/b.pdf', 'C:/other/a/b.pdf', BOUND)).toBe(false);
  });

  it('keeps the same filename in different directories apart', () => {
    expect(sameTrackedFile('foo/report.pdf', 'bar/report.pdf', BOUND)).toBe(false);
  });

  it('only trusts identical paths when the workspace root is unknown', () => {
    // 没有根就无法区分「这个 key 的绝对形态」和「别处恰好同尾的文件」，
    // 因此除完全相等外一律不合并。
    expect(sameTrackedFile('a/b.pdf', 'a/b.pdf')).toBe(true);
    expect(sameTrackedFile('a/b.pdf', '/anywhere/a/b.pdf')).toBe(false);
  });
});
