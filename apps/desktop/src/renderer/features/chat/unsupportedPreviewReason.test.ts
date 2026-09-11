import { describe, expect, it } from 'vitest';
import { unsupportedPreviewReason } from './ChatConsole';

describe('unsupportedPreviewReason', () => {
  it('detects a file that could not be opened', () => {
    expect(unsupportedPreviewReason('data.xyz', '(Could not open file: data.xyz)')).toBe(
      '文件无法打开，可能已被删除或路径无效'
    );
  });

  it('detects binary/media formats', () => {
    expect(unsupportedPreviewReason('photo.png')).toBe(
      '该文件是二进制/媒体格式，应用内无法预览其内容'
    );
    expect(unsupportedPreviewReason('archive.zip')).toBe(
      '该文件是二进制/媒体格式，应用内无法预览其内容'
    );
    expect(unsupportedPreviewReason('app.exe')).toBe(
      '该文件是二进制/媒体格式，应用内无法预览其内容'
    );
  });

  it('detects old Office formats', () => {
    expect(unsupportedPreviewReason('report.xls')).toBe(
      '该 Office 文件为旧格式，应用内暂不支持解析'
    );
    expect(unsupportedPreviewReason('slides.ppt')).toBe(
      '该 Office 文件为旧格式，应用内暂不支持解析'
    );
  });

  it('falls back to a generic message for unknown formats', () => {
    expect(unsupportedPreviewReason('notes.txt')).toBe('该文件格式暂不支持应用内预览');
    expect(unsupportedPreviewReason('data.xyz')).toBe('该文件格式暂不支持应用内预览');
    // .odt 是 OpenDocument 格式，不应归为"旧 Office"
    expect(unsupportedPreviewReason('doc.odt')).toBe('该文件格式暂不支持应用内预览');
  });
});
