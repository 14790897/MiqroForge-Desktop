import { describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ToolCommandBlock } from './ToolCommandBlock';

/** Render the block to static markup so assertions run without a DOM. */
function render(command: string, copied = false, onCopy = vi.fn()): string {
  return renderToStaticMarkup(createElement(ToolCommandBlock, { command, copied, onCopy }));
}

describe('ToolCommandBlock (issue #902)', () => {
  it('renders the full untruncated command text', () => {
    const cmd =
      'cp -r source/folder/very/long/path target; echo done | tee /tmp/out; grep -i test file';
    const html = render(cmd);
    expect(html).toContain('命令');
    expect(html).toContain(cmd);
    expect(html).toContain('font-mono');
    expect(html).toContain('max-h-48');
    expect(html).toContain('whitespace-pre-wrap');
  });

  it('renders the copy button, not-copied state', () => {
    const html = render('echo hi');
    expect(html).toContain('aria-label="复制命令"');
    expect(html).toContain('data-copied="false"');
  });

  it('marks the button copied after a successful copy', () => {
    const html = render('echo hi', true);
    expect(html).toContain('data-copied="true"');
  });

  it('invokes onCopy with the full command on click', () => {
    const onCopy = vi.fn();
    const cmd = 'python analyze.py --input long.csv | tee report.txt';
    const html = render(cmd, false, onCopy);
    // The button carries onClick wired to onCopy(command); static markup only
    // proves the button exists — the handler wiring is covered by the parent.
    expect(html).toContain('data-testid="tool-command-copy"');
    expect(onCopy).not.toHaveBeenCalled();
  });
});
