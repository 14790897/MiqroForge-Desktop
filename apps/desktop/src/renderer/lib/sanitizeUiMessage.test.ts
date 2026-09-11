import { describe, expect, it } from 'vitest';
import { sanitizeUiMessage } from './sanitizeUiMessage';

describe('sanitizeUiMessage', () => {
  it('maps a wrapped NO_API_KEY chat:send failure to a provider-config hint, not a runtime error (#617)', () => {
    const wrapped =
      "Error invoking remote method 'chat:send': No API key configured — set one in Settings > Models (NO_API_KEY)";
    expect(sanitizeUiMessage(wrapped)).toBe('未配置 API Key，请前往 设置 > 模型 配置后再试。');
  });

  it('maps the raw backend no-api-key message', () => {
    expect(
      sanitizeUiMessage(
        'No API key configured. Set one in your config file under the providers section.'
      )
    ).toBe('未配置 API Key，请前往 设置 > 模型 配置后再试。');
  });

  it('maps no_api_key code appended to the message', () => {
    expect(sanitizeUiMessage('No API key configured (NO_API_KEY)')).toBe(
      '未配置 API Key，请前往 设置 > 模型 配置后再试。'
    );
  });

  it('keeps mapping genuine bridge-down signals to the runtime hint', () => {
    expect(sanitizeUiMessage('Bridge not running')).toBe('运行时未启动或正在重启，请稍后再试。');
    expect(
      sanitizeUiMessage("Error invoking remote method 'chat:send': Bridge process exited")
    ).toBe('运行时未启动或正在重启，请稍后再试。');
    expect(
      sanitizeUiMessage(
        "Error invoking remote method 'chat:send': Bridge stopped — request cancelled"
      )
    ).toBe('运行时未启动或正在重启，请稍后再试。');
  });

  it('does NOT mask other chat:send failures as a runtime problem (#617)', () => {
    const rateLimited =
      "Error invoking remote method 'chat:send': 429 Too Many Requests (RATE_LIMITED)";
    expect(sanitizeUiMessage(rateLimited)).toContain('429');
    expect(sanitizeUiMessage(rateLimited)).not.toContain('运行时未启动');
  });

  it('maps turn-in-progress errors', () => {
    expect(sanitizeUiMessage('A turn is already in progress')).toBe(
      '上一个任务还在进行中，请稍候片刻或新开一个会话。'
    );
  });

  it('maps provider test / connection / timeout errors', () => {
    expect(sanitizeUiMessage('provider test failed')).toBe(
      '连接测试失败，请检查 API Key、API Base、模型名称或网络。'
    );
    expect(sanitizeUiMessage('Connection error: refused')).toBe(
      '连接模型服务失败，请检查网络或 API Base。'
    );
    expect(sanitizeUiMessage('Request chat.send timed out after 30000ms')).toBe(
      '请求超时，请稍后重试。'
    );
  });

  it('still strips paths, URLs and long tokens from unknown errors', () => {
    const raw =
      "Error invoking remote method 'chat:send': boom at C:\\Users\\test\\data.json https://example.com/api token" +
      'A'.repeat(48);
    const out = sanitizeUiMessage(raw);
    expect(out).toContain('[path]');
    expect(out).toContain('[url]');
    expect(out).toContain('[token]');
  });

  it('keeps model ids intact instead of mangling them into [path]', () => {
    // 实测：保存网关模型被 #929 门控拒绝时，/deepseek-v4-flash 曾被
    // 路径正则打码成 [path]，报错显示成令人费解的 deepseek[path]。
    const out = sanitizeUiMessage(
      "Error invoking remote method 'config:update': Error: Unsupported model: deepseek/deepseek-v4-flash (INVALID_PARAMS)"
    );
    expect(out).toContain('deepseek/deepseek-v4-flash');
    expect(out).not.toContain('[path]');
  });

  it('masks credential URLs with uppercase schemes (#991 review)', () => {
    const out = sanitizeUiMessage('boom at HTTPS://user:secret@example.com/path');
    expect(out).not.toContain('secret');
    expect(out).not.toContain('user');
    expect(out).toContain('[url]');
  });

  it('masks credential URLs longer than 200 chars entirely (#991 review)', () => {
    const longUrl = 'https://user:secret@example.com/' + 'a'.repeat(240);
    const out = sanitizeUiMessage('boom at ' + longUrl);
    expect(out).not.toContain('secret');
    expect(out).not.toContain('aaaa');
    expect(out).toContain('[url]');
  });
});

it('turn internal error → Chinese message', () => {
  expect(
    sanitizeUiMessage(
      "Error invoking remote method 'chat:send': Error: Turn task failed with an internal error. Check runtime logs.",
    ),
  ).toBe('任务执行失败（内部错误）。请查看运行时日志后重试。');
});
