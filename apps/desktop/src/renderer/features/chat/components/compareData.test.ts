import { describe, expect, it } from 'vitest';
import {
  extractRangeLower,
  isCompareLang,
  isRangeValue,
  parseCompareJson,
  sortParameters,
  type CompareParameter,
} from './compareData';

describe('parseCompareJson', () => {
  it('parses a full compare object', () => {
    const json = JSON.stringify({
      title: 'MOF 造粒工艺对比',
      schemes: ['路径A', '路径B'],
      parameters: [
        { name: '压力', unit: 'MPa', range: '2–4', source: 'ref-1', values: ['2–4', '5–8'] },
      ],
      citations: [{ id: 'ref-1', title: '某论文', url: 'https://example.com', doi: '10.1/x' }],
    });
    const data = parseCompareJson(json);
    expect(data?.title).toBe('MOF 造粒工艺对比');
    expect(data?.schemes).toEqual(['路径A', '路径B']);
    expect(data?.parameters).toHaveLength(1);
    expect(data?.parameters[0]).toMatchObject({
      name: '压力',
      unit: 'MPa',
      range: '2–4',
      source: 'ref-1',
      values: ['2–4', '5–8'],
    });
    expect(data?.citations).toEqual([
      { id: 'ref-1', title: '某论文', url: 'https://example.com', doi: '10.1/x' },
    ]);
  });

  it('derives schemes from the longest values array when schemes are missing', () => {
    const json = JSON.stringify({
      parameters: [
        { name: '压力', values: ['2–4', '5–8'] },
        { name: '温度', values: ['60'] },
      ],
    });
    const data = parseCompareJson(json);
    expect(data?.schemes).toEqual(['方案1', '方案2']);
  });

  it('skips invalid parameter entries (non-object / missing name)', () => {
    const json = JSON.stringify({
      schemes: ['A'],
      parameters: [null, 42, { values: ['x'] }, { name: '压力', values: ['2'] }],
    });
    const data = parseCompareJson(json);
    expect(data?.parameters).toHaveLength(1);
    expect(data?.parameters[0].name).toBe('压力');
  });

  it('returns null for malformed JSON', () => {
    expect(parseCompareJson('{not json')).toBeNull();
  });

  it('returns null for a non-object root', () => {
    expect(parseCompareJson('[1,2,3]')).toBeNull();
    expect(parseCompareJson('"str"')).toBeNull();
    expect(parseCompareJson('null')).toBeNull();
  });

  it('returns null when parameters is not an array', () => {
    expect(parseCompareJson('{"schemes":["A"]}')).toBeNull();
  });

  it('coerces non-string values to strings', () => {
    const json = JSON.stringify({ schemes: ['A'], parameters: [{ name: 'x', values: [3, null] }] });
    const data = parseCompareJson(json);
    expect(data?.parameters[0].values).toEqual(['3', '']);
  });
});

describe('extractRangeLower', () => {
  it('returns the lower bound of a range', () => {
    expect(extractRangeLower('2–4')).toBe(2);
    expect(extractRangeLower('-10~-5')).toBe(-10);
  });

  it('returns the single numeric value', () => {
    expect(extractRangeLower('5.8')).toBe(5.8);
  });

  it('returns Infinity for non-numeric / missing values', () => {
    expect(extractRangeLower('无')).toBe(Infinity);
    expect(extractRangeLower(undefined)).toBe(Infinity);
    expect(extractRangeLower('')).toBe(Infinity);
  });
});

describe('isRangeValue', () => {
  it('detects ranges across dash variants', () => {
    expect(isRangeValue('2–4')).toBe(true);
    expect(isRangeValue('3~5')).toBe(true);
    expect(isRangeValue('-10—5')).toBe(true);
  });

  it('rejects plain values', () => {
    expect(isRangeValue('5.8')).toBe(false);
    expect(isRangeValue('无')).toBe(false);
  });
});

describe('sortParameters', () => {
  const params: CompareParameter[] = [
    { name: '压力', values: ['8', '2'] },
    { name: '温度', values: ['60', '60'] },
    { name: '时间', values: ['1', '9'] },
    { name: '空值', values: [] },
  ];

  it('sorts ascending by a column using the range lower bound', () => {
    const sorted = sortParameters(params, 0, 'asc');
    expect(sorted.map((p) => p.name)).toEqual(['时间', '压力', '温度', '空值']);
  });

  it('sorts descending by a column', () => {
    const sorted = sortParameters(params, 1, 'desc');
    expect(sorted.map((p) => p.name)).toEqual(['温度', '时间', '压力', '空值']);
  });

  it('is stable and non-mutating', () => {
    const before = params.map((p) => p.name);
    const sorted = sortParameters(params, 0, 'asc');
    expect(params.map((p) => p.name)).toEqual(before);
    expect(sorted).not.toBe(params);
  });
});

describe('isCompareLang', () => {
  it('matches the compare fence languages', () => {
    expect(isCompareLang('compare')).toBe(true);
    expect(isCompareLang('compare-json')).toBe(true);
    expect(isCompareLang('COMPARE')).toBe(true);
  });

  it('rejects other languages', () => {
    expect(isCompareLang('ts')).toBe(false);
    expect(isCompareLang('json')).toBe(false);
    expect(isCompareLang('')).toBe(false);
  });
});
