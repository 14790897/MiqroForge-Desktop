import { describe, expect, it } from 'vitest';
import { lastAssistantGroupIndex, type MinimalChatGroup } from './lastAssistantGroup';

/**
 * #843 回归（CodeRabbit L9031）：末尾追加子代理行 / 重复 assistant 后，
 * 「活跃 assistant」判定必须仍指向正在生成的回答（不依赖位置 isLast）。
 */
describe('lastAssistantGroupIndex', () => {
  it('assistant 后追加子代理行（chain）仍指向该 assistant', () => {
    const groups: MinimalChatGroup[] = [
      { kind: 'msg', msg: { role: 'user' } },
      { kind: 'msg', msg: { role: 'assistant' } },
      { kind: 'chain' }, // onSubagentResult 追加的行
    ];
    expect(lastAssistantGroupIndex(groups)).toBe(1);
  });

  it('重复 assistant 追加为更后分组时指向最后一条', () => {
    const groups: MinimalChatGroup[] = [
      { kind: 'msg', msg: { role: 'assistant' } },
      { kind: 'msg', msg: { role: 'assistant' } },
    ];
    expect(lastAssistantGroupIndex(groups)).toBe(1);
  });

  it('reply-content 分组同样计入', () => {
    const groups: MinimalChatGroup[] = [
      { kind: 'reply-head' },
      { kind: 'reply-content', msg: { role: 'assistant' } },
      { kind: 'chain' },
    ];
    expect(lastAssistantGroupIndex(groups)).toBe(1);
  });

  it('以 user 结尾时指向前一个 assistant（新回合未回复前）', () => {
    const groups: MinimalChatGroup[] = [
      { kind: 'msg', msg: { role: 'assistant' } },
      { kind: 'msg', msg: { role: 'user' } },
    ];
    expect(lastAssistantGroupIndex(groups)).toBe(0);
  });

  it('无 assistant 返回 -1', () => {
    const groups: MinimalChatGroup[] = [{ kind: 'msg', msg: { role: 'user' } }];
    expect(lastAssistantGroupIndex(groups)).toBe(-1);
  });
});
