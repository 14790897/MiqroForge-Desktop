import { describe, expect, it } from 'vitest';
import { buildMergeLayout } from './SpreadsheetPreview';

describe('buildMergeLayout', () => {
  const rows = [
    ['a', 'b', 'c'],
    ['d', 'e', 'f'],
    ['g', 'h', 'i'],
  ];

  it('returns 1x1 spans for every cell when there are no merges', () => {
    const layout = buildMergeLayout(rows, undefined);
    expect(layout.length).toBe(3);
    for (const row of layout) {
      expect(row.every((c) => c && c.rowSpan === 1 && c.colSpan === 1)).toBe(true);
    }
  });

  it('marks the anchor cell with the merge size and covered cells as null', () => {
    const layout = buildMergeLayout(rows, [{ start_row: 0, start_col: 0, end_row: 1, end_col: 1 }]);
    expect(layout[0][0]).toEqual({ rowSpan: 2, colSpan: 2 });
    expect(layout[0][1]).toBeNull();
    expect(layout[1][0]).toBeNull();
    expect(layout[1][1]).toBeNull();
    expect(layout[2][2]).toEqual({ rowSpan: 1, colSpan: 1 });
  });

  it('clips merges that extend beyond the grid bounds', () => {
    const layout = buildMergeLayout(rows, [
      { start_row: 1, start_col: 1, end_row: 99, end_col: 99 },
    ]);
    expect(layout[1][1]).toEqual({ rowSpan: 2, colSpan: 2 });
    expect(layout[2][2]).toBeNull();
  });

  it('ignores degenerate 1x1 merges', () => {
    const layout = buildMergeLayout(rows, [{ start_row: 0, start_col: 0, end_row: 0, end_col: 0 }]);
    expect(layout[0][0]).toEqual({ rowSpan: 1, colSpan: 1 });
  });

  it('spans every layout row to the widest row (ragged CSV rows)', () => {
    // Rows missing trailing fields must still render empty cells so column
    // borders stay aligned (#889).
    const layout = buildMergeLayout([['a'], ['b', 'c']], undefined);
    expect(layout[0].length).toBe(2);
    expect(layout[1].length).toBe(2);
  });
});
