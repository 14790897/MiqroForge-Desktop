/**
 * #1035 复审 P1 — thread-scoped turn 的崩溃恢复。
 *
 * 这里锁住四件事：
 *  1. routing key 口径（主 tab = 基础 session；子线程 tab = `desktop:<threadId>`）；
 *  2. 恢复监听器的事件匹配口径：**只认当前 tab 的 routing key**——同一 session
 *     的另一条 key 也是别的 turn，一律拒绝（并发 turn 混流 / 抢 latch 的防线），
 *     其它 session / 其它 thread 同样拒绝；
 *  3. 恢复监听器的完整认领判定：上一条之外，本会话有 live send 或已按过停止时
 *     都不认领（turn UI 状态按会话共享，认领会冲掉用户正在看的 turn）；
 *  4. 按会话持久化：reload 后 tab 列表与选中项都能读回来，读坏/串会话不炸。
 */
import { describe, expect, it } from 'vitest';
import {
  MAIN_THREAD_ID,
  MAIN_THREAD_TAB,
  activeThreadStorageKey,
  addThreadTab,
  closeThreadTab,
  isEventForView,
  isNewRecoveredTurnStart,
  loadActiveThread,
  loadThreadState,
  loadThreadTabs,
  routingKeyFor,
  saveActiveThread,
  saveThreadTabs,
  selectThreadTab,
  sessionTurnsOf,
  shouldAdoptRecoveredEvent,
  taskTurnFor,
  threadTabsStorageKey,
  type StorageLike,
  type ThreadTab,
} from './threadTabs';

/** In-memory sessionStorage stand-in (helpers take a StorageLike). */
function fakeStorage(
  seed: Record<string, string> = {}
): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>(Object.entries(seed));
  return {
    map,
    getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k: string, v: string) => {
      map.set(k, v);
    },
  };
}

const SESSION = 'desktop:default';
const THREAD_A: ThreadTab = { threadId: 'thread-a', agentType: 'code-agent', label: '线程 A' };

describe('routing key 口径 (#1035)', () => {
  it('主 tab 用基础 session，子线程 tab 用 desktop:<threadId>', () => {
    expect(routingKeyFor(SESSION, MAIN_THREAD_ID)).toBe(SESSION);
    expect(routingKeyFor(SESSION, 'thread-a')).toBe('desktop:thread-a');
  });

  it('主 tab：只接受基础 session', () => {
    expect(isEventForView(SESSION, SESSION, MAIN_THREAD_ID)).toBe(true);
    expect(isEventForView('desktop:other', SESSION, MAIN_THREAD_ID)).toBe(false);
    expect(isEventForView('desktop:default-x', SESSION, MAIN_THREAD_ID)).toBe(false);
  });

  it('子线程 tab：只接受当前 thread 的 routing key（同期主 tab 的 turn 不并收）', () => {
    expect(isEventForView('desktop:thread-a', SESSION, 'thread-a')).toBe(true);
    // 同一 session、另一条 key 的 turn（主 tab 正在跑的那条）：这是**另一个
    // turn**，认领它就会把两条流汇进同一份 reasoning 缓冲 / 抢同一个 latch。
    expect(isEventForView(SESSION, SESSION, 'thread-a')).toBe(false);
  });

  it('并发：同一 session 的两条 key 互不认领（各自只看自己选中的那条）', () => {
    // 主 tab 上 → 只有基础 session 的事件算数，子线程 turn 的不算。
    expect(isEventForView(SESSION, SESSION, MAIN_THREAD_ID)).toBe(true);
    expect(isEventForView('desktop:thread-a', SESSION, MAIN_THREAD_ID)).toBe(false);
    // 子线程 tab 上 → 反过来。
    expect(isEventForView('desktop:thread-a', SESSION, 'thread-a')).toBe(true);
    expect(isEventForView(SESSION, SESSION, 'thread-a')).toBe(false);
  });

  it('子线程 tab：拒绝其它 thread / 其它会话的事件（跨会话、跨线程不泄漏）', () => {
    expect(isEventForView('desktop:thread-b', SESSION, 'thread-a')).toBe(false);
    expect(isEventForView('desktop:elsewhere', SESSION, 'thread-a')).toBe(false);
    expect(isEventForView('desktop:other-session', SESSION, 'thread-a')).toBe(false);
    // 注意 routing key 不含基础 session（`desktop:<threadId>`，与 handleSend 的
    // 发送口径一致）—— 不同会话之间的区分依赖 thread id 由后端全局唯一分配。
    expect(routingKeyFor('desktop:other-session', 'thread-a')).toBe(
      routingKeyFor(SESSION, 'thread-a')
    );
  });

  it('未打标（legacy）事件仍算本会话的', () => {
    expect(isEventForView(undefined, SESSION, 'thread-a')).toBe(true);
    expect(isEventForView('', SESSION, 'thread-a')).toBe(true);
  });

  it('thread id 里带冒号也不与基础 session 混淆', () => {
    expect(routingKeyFor(SESSION, 'a:b')).toBe('desktop:a:b');
    expect(isEventForView('desktop:a:b', SESSION, 'a:b')).toBe(true);
    expect(isEventForView('desktop:a', SESSION, 'a:b')).toBe(false);
  });
});

describe('按任务查在飞 turn：taskTurnFor / sessionTurnsOf (#981)', () => {
  /** 主 tab 上一条正在跑的 turn。 */
  const mainTurn = { sessionKey: SESSION, routingKey: routingKeyFor(SESSION, MAIN_THREAD_ID) };
  /** 子线程 tab 上一条正在跑的 turn。 */
  const threadATurn = { sessionKey: SESSION, routingKey: routingKeyFor(SESSION, 'thread-a') };
  const turnMap = (...turns: Array<typeof mainTurn>) =>
    new Map(turns.map((t) => [t.routingKey, t]));

  describe('taskTurnFor —— 这条 send 唯一可以 supersede（abort + await）的 turn', () => {
    it('同一个任务的重新发送：命中该任务自己的 turn', () => {
      expect(taskTurnFor(turnMap(mainTurn), SESSION, MAIN_THREAD_ID)).toBe(mainTurn);
      expect(taskTurnFor(turnMap(threadATurn), SESSION, 'thread-a')).toBe(threadATurn);
    });

    it('同一 session 的另一个 tab 发消息：查不到（切换/新建任务≠停止旧任务）', () => {
      // 主 tab 的 turn 在跑，用户在子线程 tab 里发消息 —— 旧实现按基础 session
      // 判定、且只有一个槽位，会把主 tab 的 turn abort 掉；这正是 #981 报的
      // 「切换即中断」。
      expect(taskTurnFor(turnMap(mainTurn), SESSION, 'thread-a')).toBeNull();
      // 反过来同理：子线程 tab 在跑，用户回主 tab 发消息。
      expect(taskTurnFor(turnMap(threadATurn), SESSION, MAIN_THREAD_ID)).toBeNull();
      // 两个子线程 tab 之间也不互相打断。
      expect(taskTurnFor(turnMap(threadATurn), SESSION, 'thread-b')).toBeNull();
    });

    it('并发任务各查各的：B 的 turn 不会顶掉 A 的记录（CodeRabbit 复审点）', () => {
      // 单槽实现（lifecycleRef 只存「最近一条」）会在这里失败：A 的记录被 B
      // 覆盖后，回 A 重新发送查不到 A 的 turn → 不做 supersede 直接再发一条，
      // 后端以 TURN_IN_PROGRESS 拒绝 —— 正是本 issue 要消除的错误。
      const two = turnMap(mainTurn, threadATurn);
      expect(taskTurnFor(two, SESSION, MAIN_THREAD_ID)).toBe(mainTurn);
      expect(taskTurnFor(two, SESSION, 'thread-a')).toBe(threadATurn);
    });

    it('另一个 session 的 turn：查不到（原有跨会话保护不变）', () => {
      const other = { sessionKey: 'desktop:other', routingKey: 'desktop:other' };
      expect(taskTurnFor(turnMap(other), SESSION, MAIN_THREAD_ID)).toBeNull();
    });

    it('thread id 相同但 session 不同：查不到', () => {
      // routing key `desktop:<threadId>` 不含基础 session，不同会话靠 thread id
      // 全局唯一来区分 —— 万一撞 id，sessionKey 这一半仍要挡住。
      const foreign = { sessionKey: 'desktop:other', routingKey: 'desktop:thread-a' };
      expect(taskTurnFor(turnMap(foreign), SESSION, 'thread-a')).toBeNull();
    });

    it('没有在飞的 turn（首个回合 / 已结算）：返回 null', () => {
      expect(taskTurnFor(turnMap(), SESSION, MAIN_THREAD_ID)).toBeNull();
      expect(taskTurnFor(turnMap(), SESSION, 'thread-a')).toBeNull();
    });
  });

  describe('sessionTurnsOf —— 停止按钮要中止的 turn（整个会话，不是当前那个 tab）', () => {
    it('返回本会话全部在飞的 turn，含子线程 tab 的', () => {
      const two = turnMap(mainTurn, threadATurn);
      expect(sessionTurnsOf(two, SESSION)).toEqual([mainTurn, threadATurn]);
    });

    it('不含其它会话的 turn', () => {
      const other = { sessionKey: 'desktop:other', routingKey: 'desktop:other' };
      expect(sessionTurnsOf(turnMap(mainTurn, other), SESSION)).toEqual([mainTurn]);
      expect(sessionTurnsOf(turnMap(other), SESSION)).toEqual([]);
    });

    it('没有在飞的 turn：空数组（调用方回落到基础 session 的旧行为）', () => {
      expect(sessionTurnsOf(turnMap(), SESSION)).toEqual([]);
    });
  });
});

describe('恢复监听器的认领判定 shouldAdoptRecoveredEvent (#1035)', () => {
  /** 基础场景：崩溃重载后（没有 live send、没按过停止）的干净渲染层。 */
  const clean = {
    sessionKey: SESSION,
    threadId: MAIN_THREAD_ID,
    hasLiveSend: false,
    locallyAborted: false,
  };

  it('重载后的干净渲染层：只收当前所选 tab 的那条 routing key', () => {
    expect(shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: SESSION })).toBe(true);
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:thread-a',
        threadId: 'thread-a',
      })
    ).toBe(true);
    // 并发 turn 的防线：同一 session 的另一条 key 不被认领。
    expect(
      shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: SESSION, threadId: 'thread-a' })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:thread-a',
        threadId: MAIN_THREAD_ID,
      })
    ).toBe(false);
  });

  it('会话未就绪（sessionKey 为 null）时不认领任何事件', () => {
    expect(
      shouldAdoptRecoveredEvent({ ...clean, sessionKey: null, eventSessionKey: SESSION })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({ ...clean, sessionKey: null, eventSessionKey: undefined })
    ).toBe(false);
  });

  it('仍拒绝其它 session / 其它 thread 的事件', () => {
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:other-session',
        threadId: 'thread-a',
      })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:thread-b',
        threadId: 'thread-a',
      })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: 'desktop:thread-a', threadId: 'main' })
    ).toBe(false);
  });

  // 回归防线：live send 的判定是 **session 级**的，不是 routing key 级的。
  // 组件里 streaming 标志、reasoning 缓冲/计时器、消息列表都是按会话共享的，
  // 在另一个 turn 正在跑的时候认领一条「键不同」的事件，会把用户正在看的那个
  // turn 的 UI 状态冲掉（停止按钮闪断、思考块被提前关闭）。所以只要有本会话的
  // live send 在，一律不认领——先前的 routing key 级判定正是在这里出的洞。
  it('只要本会话有 live send，就不认领（哪怕事件是另一个 routing key）', () => {
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: SESSION,
        threadId: 'thread-a',
        hasLiveSend: true,
      })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:thread-a',
        threadId: 'thread-a',
        hasLiveSend: true,
      })
    ).toBe(false);
    expect(
      shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: undefined, hasLiveSend: true })
    ).toBe(false);
  });

  it('本渲染层已经渲染过「已停止」→ 不认领（避免复活已停止的 turn）', () => {
    expect(
      shouldAdoptRecoveredEvent({
        ...clean,
        eventSessionKey: 'desktop:thread-a',
        threadId: 'thread-a',
        locallyAborted: true,
      })
    ).toBe(false);
  });

  it('未打标（legacy）事件在干净渲染层里仍算本会话的', () => {
    expect(shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: undefined })).toBe(true);
    expect(shouldAdoptRecoveredEvent({ ...clean, eventSessionKey: '' })).toBe(true);
  });
});

describe('恢复 turn 的 terminal latch：新 turn 公告判定 (#1035 复审 P1)', () => {
  const TURN_1 = 'turn-1';

  it("只有 `stream:'turn'` 且 turn_id 与已收尾的那条不同才算「新 turn」", () => {
    expect(
      isNewRecoveredTurnStart({ stream: 'turn', turnId: 'turn-2', latchedTurnId: TURN_1 })
    ).toBe(true);
  });

  it('同一 turn 的重复公告不算新 turn（不得把 latch 重新打开）', () => {
    expect(isNewRecoveredTurnStart({ stream: 'turn', turnId: TURN_1, latchedTurnId: TURN_1 })).toBe(
      false
    );
  });

  it('late progress（points / reasoning / tool 输出）一律不算——它们是已收尾 turn 的输出', () => {
    for (const stream of ['points', 'reasoning', 'stdout', 'stderr', undefined]) {
      expect(isNewRecoveredTurnStart({ stream, turnId: 'turn-2', latchedTurnId: TURN_1 })).toBe(
        false
      );
      // 带的是已收尾那条的 turn_id（points 的常见形态）同样不算
      expect(isNewRecoveredTurnStart({ stream, turnId: TURN_1, latchedTurnId: TURN_1 })).toBe(
        false
      );
      // 完全不带 turn_id 的 legacy 形态也不算
      expect(isNewRecoveredTurnStart({ stream, turnId: undefined, latchedTurnId: TURN_1 })).toBe(
        false
      );
    }
  });

  it('没有 turn_id 的公告不算（turn-id latch 就是靠它建立的，空值无从比较）', () => {
    expect(
      isNewRecoveredTurnStart({ stream: 'turn', turnId: undefined, latchedTurnId: TURN_1 })
    ).toBe(false);
    expect(isNewRecoveredTurnStart({ stream: 'turn', turnId: '', latchedTurnId: TURN_1 })).toBe(
      false
    );
  });

  it('还没 latch 过任何 turn 时，第一条带 id 的公告也算新 turn 起点', () => {
    expect(isNewRecoveredTurnStart({ stream: 'turn', turnId: TURN_1, latchedTurnId: null })).toBe(
      true
    );
  });
});

describe('thread tab 持久化 (#1035)', () => {
  it('没有存过 → 只有主 tab', () => {
    const store = fakeStorage();
    expect(loadThreadTabs(SESSION, store)).toEqual([MAIN_THREAD_TAB]);
    expect(loadActiveThread(SESSION, [MAIN_THREAD_TAB], store)).toBe(MAIN_THREAD_ID);
  });

  it('storage 不可用（null / 抛异常）时不炸，退回主 tab', () => {
    expect(loadThreadState(SESSION, null)).toEqual({
      tabs: [MAIN_THREAD_TAB],
      active: MAIN_THREAD_ID,
    });
    const hostile: StorageLike = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(loadThreadState(SESSION, hostile)).toEqual({
      tabs: [MAIN_THREAD_TAB],
      active: MAIN_THREAD_ID,
    });
    expect(() => saveThreadTabs(SESSION, [MAIN_THREAD_TAB], hostile)).not.toThrow();
    expect(() => saveActiveThread(SESSION, 'thread-a', hostile)).not.toThrow();
  });

  it('存—读往返：tab 列表与选中项都回来，且主 tab 恒在首位', () => {
    const store = fakeStorage();
    const state = addThreadTab({ tabs: [MAIN_THREAD_TAB], active: MAIN_THREAD_ID }, THREAD_A);
    saveThreadTabs(SESSION, state.tabs, store);
    saveActiveThread(SESSION, 'thread-a', store);

    const reloaded = loadThreadState(SESSION, store);
    expect(reloaded.tabs).toEqual([MAIN_THREAD_TAB, THREAD_A]);
    expect(reloaded.active).toBe('thread-a');
    // 主 tab 是状态出口的稳定不变量（UI 依赖 threads.length > 1 才渲染 tab 条）
    expect(reloaded.tabs[0].threadId).toBe(MAIN_THREAD_ID);
  });

  it('按会话隔离：A 会话的 tab 不会出现在 B 会话', () => {
    const store = fakeStorage();
    saveThreadTabs('desktop:a', [MAIN_THREAD_TAB, THREAD_A], store);
    saveActiveThread('desktop:a', 'thread-a', store);

    expect(loadThreadState('desktop:b', store)).toEqual({
      tabs: [MAIN_THREAD_TAB],
      active: MAIN_THREAD_ID,
    });
    // 两个 key 都带会话前缀
    expect(activeThreadStorageKey('desktop:a')).toBe('miqi-active-thread:desktop:a');
    expect(threadTabsStorageKey('desktop:a')).toBe('miqi-thread-tabs:desktop:a');
  });

  it('坏数据（非 JSON / 非数组 / 缺字段 / 重复）只降级、不抛', () => {
    expect(loadThreadTabs('x', fakeStorage({ 'miqi-thread-tabs:x': '{oops' }))).toEqual([
      MAIN_THREAD_TAB,
    ]);
    expect(loadThreadTabs('x', fakeStorage({ 'miqi-thread-tabs:x': '"nope"' }))).toEqual([
      MAIN_THREAD_TAB,
    ]);
    const store = fakeStorage({
      'miqi-thread-tabs:x': JSON.stringify([
        { threadId: 't1' },
        { threadId: 't1', agentType: 'dup', label: '重复' },
        { threadId: '' },
        null,
        42,
      ]),
    });
    expect(loadThreadTabs('x', store)).toEqual([
      MAIN_THREAD_TAB,
      { threadId: 't1', agentType: 'agent', label: 't1' },
    ]);
  });

  it('持久化的选中 tab 已不在列表里（列表被裁剪）→ 退回主 tab', () => {
    const store = fakeStorage({ 'miqi-active-thread:x': 'thread-gone' });
    expect(loadActiveThread('x', [MAIN_THREAD_TAB, THREAD_A], store)).toBe(MAIN_THREAD_ID);
  });

  it('关闭 tab 后不会重新出现；关掉的正是当前 tab 时退回主 tab', () => {
    const opened = addThreadTab({ tabs: [MAIN_THREAD_TAB], active: MAIN_THREAD_ID }, THREAD_A);
    const selected = selectThreadTab(opened, 'thread-a');
    expect(selected.active).toBe('thread-a');

    const closed = closeThreadTab(selected, 'thread-a');
    expect(closed.tabs).toEqual([MAIN_THREAD_TAB]);
    expect(closed.active).toBe(MAIN_THREAD_ID);

    const store = fakeStorage();
    saveThreadTabs(SESSION, closed.tabs, store);
    saveActiveThread(SESSION, closed.active, store);
    expect(loadThreadState(SESSION, store)).toEqual({
      tabs: [MAIN_THREAD_TAB],
      active: MAIN_THREAD_ID,
    });
  });

  it('重复 spawn 同一 thread 不产生重复 tab；选中不存在的 tab 被忽略', () => {
    const once = addThreadTab({ tabs: [MAIN_THREAD_TAB], active: MAIN_THREAD_ID }, THREAD_A);
    expect(addThreadTab(once, THREAD_A)).toBe(once); // 同一对象：无状态变更
    expect(selectThreadTab(once, 'thread-unknown')).toBe(once);
    expect(selectThreadTab(once, MAIN_THREAD_ID).active).toBe(MAIN_THREAD_ID);
  });
});
