import { describe, expect, it } from 'vitest';

import { computeDropLastTurns } from './ChatConsole';

type Role = 'user' | 'assistant' | 'progress' | 'error' | 'subagent';
const m = (role: Role, timestamp = 0): any => ({ role, content: 'x', timestamp });

describe('computeDropLastTurns', () => {
  it('counts user turns from the target user message to the end', () => {
    const messages = [m('user'), m('assistant'), m('user'), m('assistant')];
    expect(computeDropLastTurns(messages, 0)).toBe(2);
    expect(computeDropLastTurns(messages, 2)).toBe(1);
  });

  it('counts a single trailing user turn as 1', () => {
    const messages = [m('user'), m('assistant'), m('user')];
    expect(computeDropLastTurns(messages, 2)).toBe(1);
  });

  it('returns 0 for an assistant index with no user message after it', () => {
    const messages = [m('user'), m('assistant')];
    // index 1 is assistant — no user turns at/after it
    expect(computeDropLastTurns(messages, 1)).toBe(0);
  });

  it('ignores non-user roles when counting', () => {
    const messages = [m('user'), m('progress'), m('assistant'), m('user'), m('progress')];
    expect(computeDropLastTurns(messages, 0)).toBe(2);
    expect(computeDropLastTurns(messages, 3)).toBe(1);
  });
});
