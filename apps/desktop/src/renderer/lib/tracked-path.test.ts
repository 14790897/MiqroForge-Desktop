import { describe, expect, it } from 'vitest';

import { sameTrackedFile } from './tracked-path';

describe('sameTrackedFile (#1096)', () => {
  it('collapses a workspace-relative key against the absolute path of the same file', () => {
    // 账本存相对 key，工具消息报绝对路径 —— 用户那台机器上踩的就是这个形态。
    expect(
      sameTrackedFile('冷笑话/冷笑话合集.pdf', 'C:/Users/Guo/Desktop/test/冷笑话/冷笑话合集.pdf')
    ).toBe(true);
  });

  it('collapses the same file reported with backslashes', () => {
    expect(sameTrackedFile('sub/a.pdf', 'C:\\ws\\sub\\a.pdf')).toBe(true);
  });

  it('treats identical paths as the same file', () => {
    expect(sameTrackedFile('a/b.pdf', 'a/b.pdf')).toBe(true);
    expect(sameTrackedFile('C:/ws/a.pdf', 'C:/ws/a.pdf')).toBe(true);
  });

  it('does NOT merge two relative paths that merely share a tail', () => {
    // 同为相对 key：不同深度的同名文件是两个文件。
    expect(sameTrackedFile('a/b.pdf', 'sub/a/b.pdf')).toBe(false);
  });

  it('does NOT merge two absolute paths that merely share a tail', () => {
    // 跨根：`C:/ws/sub/a/b.pdf` 与 `C:/other/a/b.pdf` 后缀相同但不是一个文件。
    expect(sameTrackedFile('C:/ws/sub/a/b.pdf', 'C:/other/a/b.pdf')).toBe(false);
  });

  it('keeps the same filename in different directories apart', () => {
    expect(sameTrackedFile('foo/report.pdf', 'bar/report.pdf')).toBe(false);
  });

  it('does not collapse a relative path into an unrelated absolute tail', () => {
    expect(sameTrackedFile('report.pdf', 'C:/ws/nested/report.pdf')).toBe(true); // 相对 key 就是它的尾部
    expect(sameTrackedFile('nested/other.pdf', 'C:/ws/report.pdf')).toBe(false);
  });
});
