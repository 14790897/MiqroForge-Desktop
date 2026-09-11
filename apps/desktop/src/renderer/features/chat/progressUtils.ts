/** Extended progress payload with structured runtime events. */
export interface ProgressPayload {
  text?: string | null;
  tool_hint?: boolean;
  tool_call_id?: string;
  stream?: string;
  delta?: string;
  event?: string;
  data?: {
    message?: string;
    error_kind?: string;
    reason?: string;
    error?: { message?: string };
    code?: string;
    [key: string]: unknown;
  };
}

/** Parse a displayable message from a progress payload that may lack `text`. */
export function extractProgressMessage(
  payload: ProgressPayload
): { message: string; role: 'progress' | 'error' | 'warning' } | null {
  const eventName = payload.event ?? '';

  // 1) Direct text is the happy path
  if (payload.text && payload.text.trim()) {
    if (/^ExecCommand(?:Begin|End|OutputDelta)Event$/i.test(payload.text.trim())) {
      return null;
    }
    return { message: payload.text, role: 'progress' };
  }

  // 2) Structured runtime events (ErrorEvent, WarningEvent, etc.)
  const data = payload.data ?? {};

  // Exec lifecycle events are internal plumbing. Rendering them as progress
  // rows makes completed commands look like they are still spinning.
  if (/^ExecCommand(?:Begin|End|OutputDelta)Event$/.test(eventName)) {
    return null;
  }

  // Exec delta events are streamed inline — skip rendering as standalone rows.
  if (
    eventName.toLowerCase().includes('outputdelta') ||
    eventName.toLowerCase().includes('commandexecution')
  ) {
    return null;
  }

  // ToolErrorEvent is a recoverable tool-level failure the agent is expected
  // to adapt to (e.g. a path rejected by session isolation) — not a system
  // error. Rendering it with the red error bubble makes a routine
  // self-correction look like a crash; demote to a quiet warning row.
  // Turn-level errors arrive via the terminal error channel and keep red.
  if (eventName === 'ToolErrorEvent') {
    const msg = data.message ?? data.reason ?? '工具执行失败';
    if (msg) return { message: String(msg), role: 'warning' };
  }

  if (eventName.toLowerCase().includes('error') || data.error_kind) {
    const msg =
      data.message ??
      data.error?.message ??
      data.reason ??
      payload.text ??
      `${eventName || '错误'}`;
    if (msg) return { message: String(msg), role: 'error' };
  }

  if (eventName.toLowerCase().includes('warning') || eventName.toLowerCase().includes('warn')) {
    const msg = data.message ?? data.reason ?? `${eventName}`;
    if (msg) return { message: String(msg), role: 'warning' };
  }

  // 3) Unknown structured event: render as compact debug info
  if (eventName) {
    const msg = data.message ?? data.reason ?? `[${eventName}]`;
    return { message: String(msg), role: 'progress' };
  }

  // 4) Nothing displayable
  return null;
}
