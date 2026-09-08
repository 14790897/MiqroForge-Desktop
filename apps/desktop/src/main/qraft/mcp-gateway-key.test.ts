import { describe, it, expect } from 'vitest';
import { decryptMcpGatewayKey } from './mcp-gateway-key';

describe('mcp-gateway-key（内置共享网关凭据，登录时解密）', () => {
  it('解密内置密文得到非空网关 token', () => {
    const key = decryptMcpGatewayKey();
    expect(key).toBeTruthy();
    // 网关 Bearer key 形态（非空、无空白字符）
    expect(key).toMatch(/^\S{20,}$/);
  });

  it('解密结果稳定（幂等）', () => {
    expect(decryptMcpGatewayKey()).toBe(decryptMcpGatewayKey());
  });
});
