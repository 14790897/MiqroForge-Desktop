/**
 * 平台托管 MCP 网关共享凭据（全客户端同一个 token，2026-09-07 产品确认）。
 *
 * 凭据不以明文进入仓库：此处只存 AES-256-GCM 密文，登录时在主进程
 * 解密并写入 0600 token 文件（workspace/.qraft/token.json 的
 * mcpGatewayKey），Python 连接默认网关时注入 Authorization Bearer。
 *
 * 说明（已知边界）：解密钥由代码内 passphrase 派生——这是「防扫描/
 * 防明文扩散」级别的保护，不是对抗拿到仓库的攻击者的真实机密性；
 * 真实方案是平台侧 HTTPS + 按会话/用户下发凭据（届时优先使用
 * userinfo 下发的 mcpGatewayKey 覆盖本内置值）。
 */

import { createDecipheriv, scryptSync } from 'crypto';

/** AES-256-GCM 密文包（iv/tag/ct 均为 base64）。 */
const ENCRYPTED_GATEWAY_KEY = {
  iv: 'B5YixfC/CVpu/7E3',
  tag: '9etlylKwzzm7irHZ3He2Kw==',
  ct: '/OP91ondSnxfSoS6ZJqXHhI3b0ACMl0U2VDMPZmdpbOh5LPiz8tnBTOZag==',
};

const PASSPHRASE = 'miqroforge-desktop built-in slurm gateway key v1';
const SALT = 'miqroforge-mcp-gateway-salt-v1';

/** 解密内置网关凭据；失败（密文/派生参数被改动）返回 null。 */
export function decryptMcpGatewayKey(): string | null {
  try {
    const key = scryptSync(PASSPHRASE, SALT, 32);
    const decipher = createDecipheriv(
      'aes-256-gcm',
      key,
      Buffer.from(ENCRYPTED_GATEWAY_KEY.iv, 'base64')
    );
    decipher.setAuthTag(Buffer.from(ENCRYPTED_GATEWAY_KEY.tag, 'base64'));
    const plain = Buffer.concat([
      decipher.update(Buffer.from(ENCRYPTED_GATEWAY_KEY.ct, 'base64')),
      decipher.final(),
    ]);
    return plain.toString('utf8');
  } catch {
    return null;
  }
}
