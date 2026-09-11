import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ActionCard } from './ActionCard';

type Entry = {
  action: string;
  target: string;
  fileName?: string;
  sizeBytes?: number;
  sha256?: string;
  description?: string;
};

function entry(overrides: Partial<Entry> = {}): Entry {
  return {
    action: 'upload',
    target: 'Qraft',
    fileName: 'workflow.json',
    sizeBytes: 23 * 1024,
    sha256: 'deadbeef1234567890abcdef',
    ...overrides,
  };
}

function render(e: Entry): string {
  return renderToStaticMarkup(createElement(ActionCard, { entry: e, onResolve: () => {} }));
}

describe('ActionCard (#646-v2, Hermes 工具行)', () => {
  it('upload: renders target + file + size + hash + confirm', () => {
    const html = render(entry());
    expect(html).toContain('☁ 上传');
    expect(html).toContain('Qraft');
    expect(html).toContain('workflow.json');
    expect(html).toContain('23.0 KB');
    expect(html).toContain('deadbeef1234');
    expect(html).toContain('确认上传');
  });

  it('payment: distinct tone + title', () => {
    expect(render(entry({ action: 'payment' }))).toContain('💳 支付');
  });

  it('no hash → fingerprint hidden', () => {
    const html = render(entry({ sha256: undefined }));
    expect(html).not.toContain('指纹');
  });
});
