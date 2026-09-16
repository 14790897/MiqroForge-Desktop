/**
 * classifyTrackedFiles — issue #607 结果/过程资产分类
 *
 * 白名单规则（用户指定 2026-08-13）：结果文件只保留 excel/word/pdf 三类，
 * 其他（md/json/csv/html/txt/py…）一律过程。
 */
import { describe, expect, it } from 'vitest';
import {
  BULK_DIR_MIN_FILES,
  classifyTrackedFiles,
  DELIVERABLE_EXT_RE,
  dirLabel,
  groupTrackedByDir,
  isProcessFileName,
  PROCESS_FILE_NAME_RE,
} from '../src/renderer/lib/taskAssetClassification';

interface FixtureFile {
  name: string;
  op: string;
  lastSeen: number;
  /** #1104: declare_result_files 写入的显式结果标记 */
  result?: boolean;
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

describe('classifyTrackedFiles (#1104 显式声明结果文件)', () => {
  const declared = (name: string, op = 'write', lastSeen = 900): FixtureFile => ({
    name,
    op,
    lastSeen,
    result: true,
  });

  it('declared .md report → results（白名单外格式）', () => {
    const files = [declared('ZECKID_Na_report.md', 'write', 1000), f('run.log', 'write', 900)];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['ZECKID_Na_report.md']);
    expect(process.map((x) => x.name)).toEqual(['run.log']);
  });

  it('explicit declaration beats filename markers (temp_/draft_/… .md)', () => {
    const files = [declared('temp_extract.md'), declared('draft_summary.md'), f('temp_x.md', 'write', 700)];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['temp_extract.md', 'draft_summary.md']);
    expect(process.map((x) => x.name)).toEqual(['temp_x.md']);
  });

  it('delete 优先于声明——被删除的文件永不进结果区', () => {
    const files = [declared('removed_report.md', 'delete'), declared('kept_report.md', 'write', 950)];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['kept_report.md']);
    expect(process.map((x) => x.name)).toEqual(['removed_report.md']);
  });

  it('未声明的同类文件仍按白名单归类（回归）', () => {
    const files = [f('intermediate.md', 'write', 100), declared('final.md', 'write', 200)];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['final.md']);
    expect(process.map((x) => x.name)).toEqual(['intermediate.md']);
  });

  it('声明条目与白名单结果一起按 lastSeen DESC 排序', () => {
    const files = [
      f('a.pdf', 'write', 100),
      declared('b_report.md', 'write', 300),
      f('c.xlsx', 'write', 200),
    ];
    const { results } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['b_report.md', 'c.xlsx', 'a.pdf']);
  });

  it('result 非 true（undefined/false）按原白名单规则处理', () => {
    const files = [
      { name: 'x.md', op: 'write', lastSeen: 100, result: false },
      { name: 'y.pdf', op: 'write', lastSeen: 200, result: undefined },
    ];
    const { results, process } = classifyTrackedFiles(files);
    expect(results.map((x) => x.name)).toEqual(['y.pdf']);
    expect(process.map((x) => x.name)).toEqual(['x.md']);
  });
});

describe('groupTrackedByDir (#1104 批量目录折行)', () => {
  const p = (path: string) => ({ path, name: path.split('/').pop()!, op: 'write', lastSeen: 0 });

  it('目录下文件数达到阈值 → 折成一个目录组', () => {
    const files = [
      ...Array.from({ length: 20 }, (_, i) => p(`out/bvse_sites/Na_site${i + 1}.cif`)),
      p('out/summary.json'),
      p('out/Na_bvse.cube'),
    ];
    const { loose, groups } = groupTrackedByDir(files);
    expect(groups).toHaveLength(1);
    expect(groups[0].dir).toBe('out/bvse_sites');
    expect(groups[0].files).toHaveLength(20);
    expect(loose.map((f) => f.name).sort()).toEqual(['Na_bvse.cube', 'summary.json']);
  });

  it('目录下文件数少于阈值 → 保持散列卡片', () => {
    const files = [
      p('out/a.md'),
      p('out/b.md'),
      p('loose.md'),
    ];
    const { loose, groups } = groupTrackedByDir(files);
    expect(groups).toEqual([]);
    expect(loose).toHaveLength(3);
  });

  it('交付根自身的顶层产物永不折叠（即使很多）——用户明确要看这些', () => {
    const files = [
      ...Array.from({ length: 12 }, (_, i) => p(`out/file${i}.json`)),
      ...Array.from({ length: 20 }, (_, i) => p(`out/bvse_sites/Na_site${i + 1}.cif`)),
    ];
    const { loose, groups } = groupTrackedByDir(files);
    expect(groups.map((g) => g.dir)).toEqual(['out/bvse_sites']);
    expect(loose.filter((f) => f.name.endsWith('.json'))).toHaveLength(12);
  });

  it('没有产物祖先的目录不折叠（会话目录里几个文件保持散列）', () => {
    const files = [
      p('sessions/desktop_default/files/a.py'),
      p('sessions/desktop_default/files/b.json'),
      p('sessions/desktop_default/files/c.log'),
      p('sessions/desktop_default/files/d.md'),
    ];
    const { loose, groups } = groupTrackedByDir(files);
    expect(groups).toEqual([]);
    expect(loose).toHaveLength(4);
  });

  it('多个批量目录各自成组；绝对路径与反斜杠同样聚合', () => {
    const files = [
      p('C:/tmp/run/summary.json'),
      p('C:/tmp/run/analysis/a.json'),
      p('C:/tmp/run/analysis/b.json'),
      p('C:/tmp/run/analysis/c.json'),
      p('C:\\tmp\\run\\bvse_sites\\s1.cif'),
      p('C:\\tmp\\run\\bvse_sites\\s2.cif'),
      p('C:\\tmp\\run\\bvse_sites\\s3.cif'),
    ];
    const { groups, loose } = groupTrackedByDir(files);
    expect(groups.map((g) => g.dir).sort()).toEqual([
      'C:/tmp/run/analysis',
      'C:/tmp/run/bvse_sites',
    ]);
    expect(loose.map((f) => f.name)).toEqual(['summary.json']);
  });

  it('阈值常量 ≥ 3（避免把两三个文件的目录也折掉）', () => {
    expect(BULK_DIR_MIN_FILES).toBeGreaterThanOrEqual(3);
  });

  it('dirLabel 只显示末两级目录并带尾斜杠', () => {
    expect(dirLabel('C:/Users/x/run/bvse_sites')).toBe('run/bvse_sites/');
    expect(dirLabel('analysis')).toBe('analysis/');
  });
});
