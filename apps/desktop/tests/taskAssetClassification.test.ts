/**
 * classifyTrackedFiles — issue #607 结果/过程资产分类
 *
 * 白名单规则（用户指定 2026-08-13）：结果文件只保留 excel/word/pdf 三类，
 * 其他（md/json/csv/html/txt/py…）一律过程。
 */
import { describe, expect, it } from 'vitest';
import {
  classifyTrackedFiles,
  DELIVERABLE_EXT_RE,
  isProcessFileName,
  PROCESS_FILE_NAME_RE,
} from '../src/renderer/lib/taskAssetClassification';

interface FixtureFile {
  name: string;
  op: string;
  lastSeen: number;
}

const f = (name: string, op: string, lastSeen: number): FixtureFile => ({
  name,
  op,
  lastSeen,
});

describe('classifyTrackedFiles (issue #607 结果/过程资产分类 — 白名单: excel/word/pdf)', () => {
  it('empty list → no results, no process', () => {
    expect(classifyTrackedFiles([])).toEqual({ results: [], process: [] });
  });

  it('excel/word/pdf → results regardless of op (read included)', () => {
    const files = [
      f('report.pdf', 'write', 900),
      f('报表.xlsx', 'write', 910),
      f('说明文档.docx', 'edit', 920),
      f('legacy.xls', 'read', 930),
      f('notes.doc', 'read', 940),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual([
      'notes.doc',
      'legacy.xls',
      '说明文档.docx',
      '报表.xlsx',
      'report.pdf',
    ]);
    expect(process).toEqual([]);
  });

  it('非交付格式（md/json/csv/yaml/txt/py）一律过程，无论 op', () => {
    const files = [
      f('synthesis_summary.md', 'write', 900),
      f('agent_extraction.json', 'write', 910),
      f('routes.csv', 'write', 920),
      f('config.yaml', 'write', 930),
      f('notes.txt', 'read', 940),
      f('gen.py', 'edit', 950),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results).toEqual([]);
    expect(process.map((x) => x.name)).toEqual([
      'synthesis_summary.md',
      'agent_extraction.json',
      'routes.csv',
      'config.yaml',
      'notes.txt',
      'gen.py',
    ]);
  });

  it('delete → 过程，即使三类格式', () => {
    const files = [f('old_report.pdf', 'delete', 900), f('new_report.pdf', 'write', 910)];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['new_report.pdf']);
    expect(process.map((x) => x.name)).toEqual(['old_report.pdf']);
  });

  it('filename markers → process even for deliverable formats (temp/tmp/…/.log)', () => {
    const files = [
      f('temp_report.pdf', 'write', 500),
      f('tmp_data.xlsx', 'write', 600),
      f('debug_note.docx', 'write', 700),
      f('step1_extract.py', 'write', 800),
      f('real_report.pdf', 'write', 900),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['real_report.pdf']);
    expect(process.map((x) => x.name)).toEqual([
      'temp_report.pdf',
      'tmp_data.xlsx',
      'debug_note.docx',
      'step1_extract.py',
    ]);
  });

  it('marker regex is conservative: step_by_step_guide.md must NOT match', () => {
    expect(isProcessFileName('step_by_step_guide.md')).toBe(false);
    expect(isProcessFileName('report.log')).toBe(true);
  });

  it('results sorted lastSeen DESC (newest first); process sorted ASC', () => {
    const files = [
      f('a.pdf', 'write', 100),
      f('b.xlsx', 'write', 300),
      f('c.docx', 'write', 200),
      f('n1.md', 'write', 400),
      f('n2.csv', 'write', 500),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['b.xlsx', 'c.docx', 'a.pdf']);
    expect(process.map((x) => x.name)).toEqual(['n1.md', 'n2.csv']);
  });

  it('用户真实会话（desktop_1786603974326）：3 个 pdf/xlsx 交付物 → 结果，json/csv/md → 过程', () => {
    const files = [
      f('Gold试剂_试剂价格与成本核算.xlsx', 'read', 1000),
      f('gold_reagent_feasibility_report.pdf', 'read', 990),
      f('Gold试剂_CAS1071-38-1_合成路线与可行性报告.pdf', 'read', 980),
      f('gold_reagent_synthesis_extraction.json', 'read', 970),
      f('gold_reagent_reagent_pricing.csv', 'read', 960),
      f('gold_reagent_feasibility_report.md', 'read', 950),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name).sort()).toEqual([
      'Gold试剂_CAS1071-38-1_合成路线与可行性报告.pdf',
      'Gold试剂_试剂价格与成本核算.xlsx',
      'gold_reagent_feasibility_report.pdf',
    ]);
    expect(process.map((x) => x.name).sort()).toEqual([
      'gold_reagent_feasibility_report.md',
      'gold_reagent_reagent_pricing.csv',
      'gold_reagent_synthesis_extraction.json',
    ]);
  });

  it('svg/html 渲染产物（graph_render #715）→ 结果资产，json 仍为过程', () => {
    const files = [
      f('step-graph.json', 'read', 1000),
      f('data-graph.json', 'read', 990),
      f('step-graph.svg', 'write', 980),
      f('data-graph.svg', 'write', 970),
      f('step-graph.html', 'write', 960),
      f('data-graph.html', 'write', 950),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual([
      'step-graph.svg',
      'data-graph.svg',
      'step-graph.html',
      'data-graph.html',
    ]);
    expect(process.map((x) => x.name)).toEqual(['data-graph.json', 'step-graph.json']);
  });

  it('MOF 流程产物：synthesis_summary.md/routes.csv/feasibility.json 等归过程，pdf/html 为结果', () => {
    const files = [
      f('synthesis_summary.md', 'write', 1300),
      f('routes.csv', 'write', 1310),
      f('reagents.csv', 'write', 1320),
      f('report.html', 'write', 1330),
      f('feasibility.json', 'write', 1340),
      f('最终报告.pdf', 'write', 1350),
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['最终报告.pdf', 'report.html']);
    expect(process).toHaveLength(4);
  });

  it('DELIVERABLE_EXT_RE 匹配 excel/word/pdf/svg/html', () => {
    expect(DELIVERABLE_EXT_RE.test('a.pdf')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.doc')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.docx')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.xls')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.xlsx')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.svg')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.html')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.htm')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.pptx')).toBe(false);
    expect(DELIVERABLE_EXT_RE.test('a.md')).toBe(false);
    expect(DELIVERABLE_EXT_RE.test('a.csv')).toBe(false);
    expect(DELIVERABLE_EXT_RE.test('a.json')).toBe(false);
    expect(DELIVERABLE_EXT_RE.test('a.PDF')).toBe(true);
    expect(DELIVERABLE_EXT_RE.test('a.SVG')).toBe(true);
  });

  it('PROCESS_FILE_NAME_RE 与白名单互不干扰（标记优先于三类）', () => {
    expect(PROCESS_FILE_NAME_RE.test('temp.pdf')).toBe(true);
    expect(PROCESS_FILE_NAME_RE.test('最终报告.pdf')).toBe(false);
    expect(DELIVERABLE_EXT_RE.test('temp.pdf')).toBe(true);
  });
});
