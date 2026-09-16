/**
 * welcomeScenes 数据约束测试（#962 评审 P2）。
 *
 * 这个文件以后很可能被动态数据替换，而产品约束（每场景 3–5 个任务、字段非空）光靠
 * TypeScript 守不住——改数据的人只动数据，CI 照样全绿。这里把约束固化下来。
 *
 * 另有一条运行时依赖也在这里守：ChatConsole 的选中态是用 title 记身份的（不是下标，
 * 见 pickedSceneTitle 的注释，修的是「异步 prepend 把下标挤位」的 #962 评审 P1），
 * 所以 title 必须唯一。
 */
import { describe, expect, it } from 'vitest';
import {
  MODE_SCENES,
  SKILL_ORDER,
  SKILL_SCENE_ICON,
  SKILL_SCENE_TITLE,
  SKILL_STARTERS,
  type StarterTask,
} from './welcomeScenes';

const MODES = ['fast', 'daily', 'code'] as const;
const FIELDS: (keyof StarterTask)[] = ['title', 'icon', 'scenario', 'deliverable', 'ask', 'needs'];
/** 变体选择符 / ZWJ / 肤色 / 区域指示符：跨平台渲染不稳，一律不用 */
const EMOJI_UNSAFE = /[\uFE0F\u200D\u{1F3FB}-\u{1F3FF}\u{1F1E6}-\u{1F1FF}]/u;

/**
 * 先卡类型再 trim：`String(undefined)` 会变成 "undefined"、`String(null)` 变成 "null"，
 * 两者都能骗过「非空」断言（#962 CodeRabbit）。类型不对时第一条断言就红，而不是走到
 * `.trim()` 上抛 TypeError。
 */
function assertNonEmptyString(value: unknown, where: string) {
  expect(typeof value, where).toBe('string');
  expect((value as string).trim(), where).not.toBe('');
}

const allScenes = MODES.flatMap((m) => MODE_SCENES[m].map((s) => [`${m}/${s.title}`, s] as const));

describe('MODE_SCENES', () => {
  it('三种做事方式都有场景', () => {
    expect(Object.keys(MODE_SCENES).sort()).toEqual([...MODES].sort());
    for (const m of MODES) expect(MODE_SCENES[m].length, m).toBeGreaterThan(0);
  });

  it('同一档里场景名不重复（选中态按 title 记身份）', () => {
    for (const m of MODES) {
      const titles = MODE_SCENES[m].map((s) => s.title);
      expect(new Set(titles).size, m).toBe(titles.length);
    }
  });

  it('每个场景 3–5 个任务（产品要求）', () => {
    for (const [where, s] of allScenes) {
      expect(s.tasks.length, where).toBeGreaterThanOrEqual(3);
      expect(s.tasks.length, where).toBeLessThanOrEqual(5);
    }
  });

  it('同一场景里任务名不重复', () => {
    for (const [where, s] of allScenes) {
      const titles = s.tasks.map((t) => t.title);
      expect(new Set(titles).size, where).toBe(titles.length);
    }
  });

  it('字段非空，图标是单码位 emoji', () => {
    for (const [where, s] of allScenes) {
      expect(s.icon.trim(), where).not.toBe('');
      expect([...s.icon].length, `${where} 的场景图标不是单码位`).toBe(1);
      for (const t of s.tasks) {
        for (const f of FIELDS) {
          assertNonEmptyString(t[f], `${where} › ${t.title} › ${f}`);
        }
        expect(t.icon, `${where} › ${t.title}`).not.toMatch(EMOJI_UNSAFE);
        expect([...t.icon].length, `${where} › ${t.title} 的图标不是单码位`).toBe(1);
      }
    }
  });

  it('场景名不跟「内置技能」那一项撞名', () => {
    for (const [where, s] of allScenes) {
      expect(s.title, where).not.toBe(SKILL_SCENE_TITLE);
    }
    expect([...SKILL_SCENE_ICON].length).toBe(1);
  });
});

describe('SKILL_ORDER / SKILL_STARTERS', () => {
  it('白名单里的名字都在文案表里，且不重复', () => {
    expect(new Set(SKILL_ORDER).size).toBe(SKILL_ORDER.length);
    for (const name of SKILL_ORDER) expect(SKILL_STARTERS[name], name).toBeDefined();
  });

  it('上架文案的六个字段都不为空，图标是单码位 emoji', () => {
    for (const name of SKILL_ORDER) {
      const copy = SKILL_STARTERS[name];
      for (const f of FIELDS) {
        assertNonEmptyString(copy[f], `${name} › ${f}`);
      }
      expect(copy.icon, name).not.toMatch(EMOJI_UNSAFE);
      expect([...copy.icon].length, `${name} 的图标不是单码位`).toBe(1);
    }
  });

  it('上架文案的 title 也不重复（会成为 L3 卡片的身份）', () => {
    const titles = SKILL_ORDER.map((n) => SKILL_STARTERS[n].title);
    expect(new Set(titles).size).toBe(titles.length);
  });
});
