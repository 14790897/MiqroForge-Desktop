/**
 * Frame-aware sends to the renderer.
 *
 * When the renderer process dies (OOM, crash), the `BrowserWindow` and its
 * `WebContents` both stay alive — `win.isDestroyed()` and `wc.isDestroyed()`
 * return `false` — but the render **frame** is gone. Every subsequent
 * `webContents.send()` then fails inside Electron:
 *
 *   Error sending from webFrameMain: Error: Render frame was disposed before
 *   WebFrameMain could be accessed
 *
 * Electron logs that line itself and does **not** rethrow to the caller, so a
 * `try { wc.send(...) } catch {}` catches nothing. Verified against Electron
 * 39.8.10: after `forcefullyCrashRenderer()`, 20 `send()` calls produced 0
 * throws and 20 internal log lines. Guarding on window/WebContents liveness is
 * therefore not enough — the send has to be skipped *before* it happens, or it
 * keeps writing one line per event (#1019: 15,413 lines in a single turn,
 * ~29/s sustained).
 *
 * The frame is the thing to test: `wc.mainFrame.isDestroyed()` is `false`
 * while the frame is live and `true` once it is disposed, without throwing,
 * and it flips back to `false` after `wc.reload()`. That keeps the check
 * stateless — there is no per-WebContents "dead" flag to set, and no stale
 * flag to clear when a renderer comes back.
 *
 * Known limit: `mainFrame.isDestroyed()` tracks frame disposal, not renderer
 * liveness. When the renderer is killed externally the process is gone
 * (`isCrashed()` is true, `getOSProcessId()` is 0) while the frame object
 * survives and still reports `false` — so a send there is attempted and
 * quietly does nothing. That costs one call and produces no log line, i.e.
 * no flood, which is the failure this module exists to stop; it is not a
 * delivery guarantee.
 *
 * Typed structurally so it unit-tests without importing Electron.
 */

export interface SendableContents {
  isDestroyed(): boolean;
  /** Absent on a destroyed WebContents; nullish counts as not alive. */
  mainFrame?: { isDestroyed(): boolean } | null;
  send(channel: string, data: unknown): void;
}

/**
 * True when `contents` can actually receive a message: the WebContents still
 * exists and its main frame has not been disposed.
 */
export function isFrameAlive(contents: SendableContents | null | undefined): boolean {
  if (!contents || contents.isDestroyed()) return false;
  try {
    return contents.mainFrame?.isDestroyed() === false;
  } catch {
    // On a torn-down WebContents the mainFrame getter throws ("Object has been
    // destroyed") — but only in states where isDestroyed() already returned
    // true above, so this is belt-and-braces rather than an expected path.
    // Kept because swallowing the throw here beats letting it escape into
    // event-forwarding code.
    return false;
  }
}

/**
 * Send `data` to `contents` only when its frame is alive. Returns whether the
 * send actually happened, so callers and tests can tell a skipped send from a
 * delivered one.
 */
export function sendToFrame(
  contents: SendableContents | null | undefined,
  channel: string,
  data: unknown
): boolean {
  if (!contents || !isFrameAlive(contents)) return false;
  contents.send(channel, data);
  return true;
}

/**
 * Convenience wrapper for the common "one window" case: skips a null or
 * destroyed window, then applies the frame check above.
 */
export function sendToWindow(
  win: { isDestroyed(): boolean; webContents: SendableContents } | null | undefined,
  channel: string,
  data: unknown
): boolean {
  if (!win || win.isDestroyed()) return false;
  return sendToFrame(win.webContents, channel, data);
}
