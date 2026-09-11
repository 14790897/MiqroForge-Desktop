import { describe, expect, it } from 'vitest';
import {
  extractThreadListRows,
  pickThreadToResume,
} from '../src/renderer/features/chat/ChatConsole';

/**
 * Issue #490: when (re)entering a session the frontend must resume the
 * session's existing thread instead of calling thread/start and minting a
 * fresh random thread_id. pickThreadToResume selects that thread from a
 * `thread/list` result. These tests pin the selection rule so the resume
 * path keeps loading A's own history — without ever pulling B/C content
 * into A.
 *
 * Selection rule (revised for fragmented legacy sessions): prefer the
 * thread with the MOST persisted turns (`turnCount`), ties broken by the
 * largest `updatedAt`. On a clean single-thread session both heuristics
 * agree; on a fragmented session the richness rule lands on the thread
 * that holds the real conversation rather than a nearly-empty thread that
 * was merely touched last.
 */
describe('pickThreadToResume', () => {
  it('returns null when there are no threads (brand-new session)', () => {
    expect(pickThreadToResume([])).toBeNull();
    expect(pickThreadToResume(null)).toBeNull();
    expect(pickThreadToResume(undefined)).toBeNull();
    expect(pickThreadToResume('not-an-array')).toBeNull();
  });

  it('picks the single active thread', () => {
    const items = [
      { id: 'thread-a', turnCount: 3, updatedAt: 1000, archived: false, ephemeral: false },
    ];
    expect(pickThreadToResume(items)).toBe('thread-a');
  });

  it('prefers the thread with the most turns even if it is not the most recent', () => {
    // Real fragmented-session shape: a nearly-empty recap thread touched
    // last vs. a history-rich thread touched earlier.
    const items = [
      {
        id: 'rich-but-older',
        turnCount: 14,
        updatedAt: 2000,
        createdAt: 1000,
        archived: false,
        ephemeral: false,
      },
      {
        id: 'empty-but-recent',
        turnCount: 1,
        updatedAt: 9000,
        createdAt: 8000,
        archived: false,
        ephemeral: false,
      },
      {
        id: 'mid',
        turnCount: 6,
        updatedAt: 5000,
        createdAt: 1500,
        archived: false,
        ephemeral: false,
      },
    ];
    expect(pickThreadToResume(items)).toBe('rich-but-older');
  });

  it('breaks turnCount ties by the largest updatedAt', () => {
    const items = [
      {
        id: 'same-turns-old',
        turnCount: 5,
        updatedAt: 1000,
        createdAt: 1000,
        archived: false,
        ephemeral: false,
      },
      {
        id: 'same-turns-new',
        turnCount: 5,
        updatedAt: 5000,
        createdAt: 2000,
        archived: false,
        ephemeral: false,
      },
      {
        id: 'same-turns-mid',
        turnCount: 5,
        updatedAt: 3000,
        createdAt: 1500,
        archived: false,
        ephemeral: false,
      },
    ];
    expect(pickThreadToResume(items)).toBe('same-turns-new');
  });

  it('treats a missing turnCount as 0 (richness still drives selection)', () => {
    const items = [
      { id: 'no-count', updatedAt: 9000, createdAt: 9000, archived: false, ephemeral: false },
      {
        id: 'has-count',
        turnCount: 2,
        updatedAt: 1000,
        createdAt: 1000,
        archived: false,
        ephemeral: false,
      },
    ];
    expect(pickThreadToResume(items)).toBe('has-count');
  });

  it('falls back to createdAt when updatedAt is missing (richness still wins)', () => {
    const items = [
      { id: 'thread-x', turnCount: 4, createdAt: 1000, archived: false, ephemeral: false },
      { id: 'thread-y', turnCount: 1, createdAt: 9000, archived: false, ephemeral: false },
    ];
    expect(pickThreadToResume(items)).toBe('thread-x');
  });

  it('skips archived threads', () => {
    const items = [
      {
        id: 'archived-but-richest',
        turnCount: 99,
        updatedAt: 9999,
        archived: true,
        ephemeral: false,
      },
      { id: 'active-thin', turnCount: 1, updatedAt: 1000, archived: false, ephemeral: false },
    ];
    expect(pickThreadToResume(items)).toBe('active-thin');
  });

  it('skips ephemeral threads', () => {
    const items = [
      { id: 'ephemeral-richest', turnCount: 99, updatedAt: 9999, archived: false, ephemeral: true },
      { id: 'persistent', turnCount: 1, updatedAt: 1000, archived: false, ephemeral: false },
    ];
    expect(pickThreadToResume(items)).toBe('persistent');
  });

  it('treats archived/ephemeral as falsey when absent (only archived:false survives)', () => {
    // Mirrors real ThreadView rows where archived/ephemeral are booleans.
    const items = [
      { id: 'only-id', turnCount: 2, updatedAt: 1234 }, // archived/ephemeral absent → not filtered out
    ];
    expect(pickThreadToResume(items)).toBe('only-id');
  });

  it('returns null when every thread is archived or ephemeral', () => {
    const items = [
      { id: 'a', turnCount: 9, updatedAt: 1000, archived: true, ephemeral: false },
      { id: 'b', turnCount: 9, updatedAt: 2000, archived: false, ephemeral: true },
    ];
    expect(pickThreadToResume(items)).toBeNull();
  });

  it('ignores rows with a missing or empty id', () => {
    const items = [
      { id: '', turnCount: 99, updatedAt: 9999, archived: false, ephemeral: false },
      { turnCount: 98, updatedAt: 9998, archived: false, ephemeral: false },
      { id: 'valid', turnCount: 1, updatedAt: 1, archived: false, ephemeral: false },
    ];
    expect(pickThreadToResume(items)).toBe('valid');
  });

  it('handles stringy/non-numeric counts and timestamps defensively', () => {
    const items = [
      {
        id: 'a',
        turnCount: 'abc',
        updatedAt: 'abc',
        createdAt: 5,
        archived: false,
        ephemeral: false,
      },
      {
        id: 'b',
        turnCount: '10',
        updatedAt: '10',
        createdAt: 1,
        archived: false,
        ephemeral: false,
      },
    ];
    // Number('abc') → NaN → 0; Number('10') → 10. b has more turns → picked.
    expect(pickThreadToResume(items)).toBe('b');
  });
});

/**
 * Wiring-level coverage: exercise the real `threads.list → extractThreadListRows
 * → pickThreadToResume` flow the ChatConsole load effect runs, with payloads
 * shaped like the actual backend response (Page.to_dict = { data, nextCursor },
 * with the ThreadView camelCase fields). This catches a field-name or envelope
 * mismatch between the backend and the resume helper that pure-helper tests
 * (above) can't see — the failure that originally made #490's resume a no-op.
 */
describe('resume wiring: threads.list → pickThreadToResume', () => {
  it('resumes the history-richest thread from a backend Page envelope', () => {
    // Mirrors the real _thread_list response: { data: [ThreadView.to_dict...] }.
    const backendResponse = {
      data: [
        {
          id: 'thread-empty-recap',
          title: 'recap',
          status: 'active',
          turnCount: 1,
          updatedAt: 9000,
          createdAt: 8500,
          archived: false,
          ephemeral: false,
        },
        {
          id: 'thread-real-convo',
          title: 'main',
          status: 'active',
          turnCount: 14,
          updatedAt: 2000,
          createdAt: 1000,
          archived: false,
          ephemeral: false,
        },
        {
          id: 'thread-archived-old',
          title: 'old',
          status: 'archived',
          turnCount: 99,
          updatedAt: 9999,
          createdAt: 1,
          archived: true,
          ephemeral: false,
        },
      ],
      nextCursor: null,
    };
    const rows = extractThreadListRows(backendResponse);
    expect(pickThreadToResume(rows)).toBe('thread-real-convo');
  });

  it('still resumes when the backend uses the legacy `items` envelope', () => {
    const legacyResponse = {
      items: [{ id: 'only-one', turnCount: 5, updatedAt: 1000, archived: false, ephemeral: false }],
    };
    expect(pickThreadToResume(extractThreadListRows(legacyResponse))).toBe('only-one');
  });

  it('returns null for an empty page (brand-new session resumes nothing)', () => {
    expect(pickThreadToResume(extractThreadListRows({ data: [], nextCursor: null }))).toBeNull();
  });

  it('returns null when the envelope is malformed / unexpected shape', () => {
    // Defends against a backend change that drops both `data` and `items`:
    // resume must no-op (not throw) so the load effect still reaches
    // setHistoryLoaded(true) and falls back to thread/start on first send.
    expect(pickThreadToResume(extractThreadListRows(null))).toBeNull();
    expect(pickThreadToResume(extractThreadListRows(undefined))).toBeNull();
    expect(pickThreadToResume(extractThreadListRows({ unexpected: 'shape' }))).toBeNull();
    expect(pickThreadToResume(extractThreadListRows({ data: 'not-an-array' }))).toBeNull();
  });

  it('exhibits no cross-session leakage: only the listed (own-session) threads are ever candidates', () => {
    // Isolation guarantee for #490: the resume helper only ever sees threads
    // for THIS session (threads.list is session-scoped server-side), so B/C
    // ids can never appear as a selection here. Pin that only the supplied
    // rows are considered — nothing is fetched or merged from elsewhere.
    const ownSessionOnly = {
      data: [{ id: 'A-thread', turnCount: 10, updatedAt: 1000, archived: false, ephemeral: false }],
    };
    expect(pickThreadToResume(extractThreadListRows(ownSessionOnly))).toBe('A-thread');
  });
});
