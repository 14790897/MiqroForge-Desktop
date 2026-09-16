import { describe, expect, it, vi } from 'vitest';
import { isFrameAlive, sendToFrame, sendToWindow, type SendableContents } from './frame-send';

/**
 * Stand-in for WebContents. `frameDestroyed` models the state that matters:
 * the renderer is gone but the WebContents object is still alive — the exact
 * combination that made `!wc.isDestroyed()` let #1019 through.
 */
function makeContents(opts: { destroyed?: boolean; frameDestroyed?: boolean } = {}) {
  const send = vi.fn();
  const frame = { isDestroyed: () => opts.frameDestroyed === true };
  const contents: SendableContents = {
    isDestroyed: () => opts.destroyed === true,
    send,
  };
  if (opts.frameDestroyed !== undefined) contents.mainFrame = frame;
  return { contents, send };
}

describe('isFrameAlive', () => {
  it('is true while the frame is live', () => {
    const { contents } = makeContents({ destroyed: false, frameDestroyed: false });
    expect(isFrameAlive(contents)).toBe(true);
  });

  it('is false once the render frame is disposed, even though the WebContents is alive', () => {
    const { contents } = makeContents({ destroyed: false, frameDestroyed: true });
    expect(contents.isDestroyed()).toBe(false); // the guard that used to be here
    expect(isFrameAlive(contents)).toBe(false);
  });

  it('is false for a destroyed WebContents', () => {
    const { contents } = makeContents({ destroyed: true, frameDestroyed: false });
    expect(isFrameAlive(contents)).toBe(false);
  });

  it('is false when mainFrame is absent', () => {
    const { contents } = makeContents({ destroyed: false });
    expect(isFrameAlive(contents)).toBe(false);
  });

  it('is false, not a throw, when reading mainFrame throws', () => {
    const contents = {
      isDestroyed: () => false,
      get mainFrame(): { isDestroyed(): boolean } {
        throw new Error('Render frame was disposed before WebFrameMain could be accessed');
      },
      send: vi.fn(),
    };
    expect(() => isFrameAlive(contents)).not.toThrow();
    expect(isFrameAlive(contents)).toBe(false);
  });

  it('is false for null and undefined', () => {
    expect(isFrameAlive(null)).toBe(false);
    expect(isFrameAlive(undefined)).toBe(false);
  });
});

describe('sendToFrame', () => {
  it('delivers and reports true when the frame is alive', () => {
    const { contents, send } = makeContents({ frameDestroyed: false });
    expect(sendToFrame(contents, 'chat:progress', { n: 1 })).toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith('chat:progress', { n: 1 });
  });

  it('never calls send once the frame is disposed (#1019: one log line per event)', () => {
    const { contents, send } = makeContents({ destroyed: false, frameDestroyed: true });
    for (let i = 0; i < 500; i++) {
      expect(sendToFrame(contents, 'chat:progress', { i })).toBe(false);
    }
    expect(send).not.toHaveBeenCalled();
  });

  it('skips null contents', () => {
    expect(sendToFrame(null, 'runtime:log', 'x')).toBe(false);
  });
});

describe('sendToWindow', () => {
  it('delivers through a live window', () => {
    const { contents, send } = makeContents({ frameDestroyed: false });
    const win = { isDestroyed: () => false, webContents: contents };
    expect(sendToWindow(win, 'runtime:state', { state: 'running' })).toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('skips a destroyed window without touching its webContents', () => {
    const { contents, send } = makeContents({ frameDestroyed: false });
    const win = { isDestroyed: () => true, webContents: contents };
    expect(sendToWindow(win, 'runtime:state', {})).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it('skips a live window whose frame is disposed', () => {
    const { contents, send } = makeContents({ destroyed: false, frameDestroyed: true });
    const win = { isDestroyed: () => false, webContents: contents };
    expect(sendToWindow(win, 'runtime:state', {})).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it('skips null and undefined windows', () => {
    expect(sendToWindow(null, 'runtime:log', 'x')).toBe(false);
    expect(sendToWindow(undefined, 'runtime:log', 'x')).toBe(false);
  });
});

describe('reload recovery', () => {
  it('delivers again once a fresh frame is live (no sticky "dead" state)', () => {
    const contents: SendableContents = {
      isDestroyed: () => false,
      mainFrame: { isDestroyed: () => true },
      send: vi.fn(),
    };
    expect(sendToFrame(contents, 'runtime:state', {})).toBe(false);

    // wc.reload(): same WebContents, new frame.
    contents.mainFrame = { isDestroyed: () => false };
    expect(sendToFrame(contents, 'runtime:state', {})).toBe(true);
  });
});

describe('the #1019 trigger direction: live -> dead on the same WebContents', () => {
  it('stops delivering the instant the frame goes away, mid-stream', () => {
    const contents: SendableContents = {
      isDestroyed: () => false,
      mainFrame: { isDestroyed: () => false },
      send: vi.fn(),
    };

    // Events flow normally...
    expect(sendToFrame(contents, 'chat:progress', { i: 0 })).toBe(true);

    // ...then the renderer dies (OOM/crash): same WebContents object, frame gone.
    contents.mainFrame = { isDestroyed: () => true };

    for (let i = 1; i <= 1000; i++) {
      expect(sendToFrame(contents, 'chat:progress', { i })).toBe(false);
    }
    // Exactly the one send from before the crash reached the renderer.
    expect(contents.send).toHaveBeenCalledTimes(1);
  });

  it('keeps up across repeated crash -> reload -> crash cycles', () => {
    const contents: SendableContents = {
      isDestroyed: () => false,
      mainFrame: { isDestroyed: () => false },
      send: vi.fn(),
    };

    for (let cycle = 0; cycle < 3; cycle++) {
      expect(sendToFrame(contents, 'runtime:state', { cycle })).toBe(true);
      contents.mainFrame = { isDestroyed: () => true };
      expect(sendToFrame(contents, 'runtime:state', { cycle })).toBe(false);
      contents.mainFrame = { isDestroyed: () => false };
    }

    expect(contents.send).toHaveBeenCalledTimes(3);
  });
});
