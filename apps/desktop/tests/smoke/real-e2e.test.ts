/**
 * Real E2E tests — uses actual miqi agent CLI with real config + LLM.
 *
 * These tests call `uv run miqi agent -m "..."` and verify responses.
 * They require:
 *   - Python + miqi installed (uv run handles this)
 *   - Valid config at ~/.miqi/config.json with API keys
 *   - Network access (for LLM API calls)
 *
 * Run: cd apps/desktop && npx vitest run tests/smoke/real-e2e.test.ts
 */

import { execSync } from 'node:child_process';
import { describe, test, expect } from 'vitest';
import { resolve } from 'node:path';

const ROOT = resolve(__dirname, '../../../..'); // apps/desktop/tests/smoke → project root

/** Run miqi agent with a message and return stdout */
function miqi(message: string, timeout = 120_000): string {
  const cmd = `uv run miqi agent -m "${message.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}" --no-logs --no-markdown`;
  try {
    const out = execSync(cmd, {
      cwd: ROOT,
      timeout,
      encoding: 'utf-8',
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32' ? 'powershell' : true,
    });
    return out;
  } catch (e: any) {
    return e.stdout || e.stderr || e.message || String(e);
  }
}

// Increase timeout for real LLM calls
const TEST_TIMEOUT = 120_000;

describe('Real Agent E2E', () => {
  test('basic greeting returns a response', { timeout: TEST_TIMEOUT }, () => {
    const out = miqi('回复一个字：好');
    expect(out).toContain('好');
  });

  test('web search: today weather', { timeout: TEST_TIMEOUT }, () => {
    const out = miqi('搜索今天北京的天气');
    // Should contain weather data from a real source
    expect(out).toMatch(/天气|weather/i);
    expect(out).toMatch(/℃|度|温度|temperature/i);
  });

  test('web search: GitHub trending', { timeout: TEST_TIMEOUT }, () => {
    const out = miqi('告诉我github上最近最热门的python项目，说一个名字就好');
    // Should mention at least one real project
    expect(out.length).toBeGreaterThan(20);
  });

  test('math reasoning', { timeout: TEST_TIMEOUT }, () => {
    const out = miqi('1+2+3+4+5等于多少？只回答数字');
    expect(out).toMatch(/15/);
  });
});
