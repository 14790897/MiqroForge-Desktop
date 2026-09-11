/**
 * RSA 公钥提取与密码加密（MiQroForge 平台登录）。
 *
 * 实测要点（《Qraft OAuth2 接入实测文档》）：
 * - 密码必须用 RSA 公钥加密后 Base64 传输，明文会登录失败；
 * - 算法为 PKCS#1 v1.5 填充（与 JSEncrypt 默认一致），Node 的
 *   crypto.publicEncrypt 使用 RSA_PKCS1_PADDING 与之完全一致；
 * - 公钥从前端 JS bundle（登录页 /login 的 era-index-*.js）动态提取
 *   （grep `BEGIN PUBLIC KEY`），避免硬编码随发版失效。
 */

import { publicEncrypt, constants } from 'node:crypto';

export const PUBLIC_KEY_BLOCK_RE =
  /-----BEGIN PUBLIC KEY-----[\sA-Za-z0-9+/=]+-----END PUBLIC KEY-----/;

/** 用 PKCS#1 v1.5 填充 RSA 加密，结果 Base64（等价 JSEncrypt 默认行为）。 */
export function encryptPasswordRsa(password: string, publicKeyPem: string): string {
  const encrypted = publicEncrypt(
    {
      key: publicKeyPem,
      padding: constants.RSA_PKCS1_PADDING,
    },
    Buffer.from(password, 'utf8')
  );
  return encrypted.toString('base64');
}

/**
 * 从 JS bundle 内容中提取第一个 PEM 公钥块。
 * bundle 里公钥可能以字符串字面量形式存在（换行被转义为 \n），
 * 匹配前先归一化，返回标准 PEM（真实换行）。
 */
export function extractPublicKeyFromBundle(bundleContent: string): string | null {
  const normalized = bundleContent.replace(/\\n/g, '\n').replace(/\\r/g, '\r');
  const match = normalized.match(PUBLIC_KEY_BLOCK_RE);
  return match ? match[0] : null;
}

/**
 * 从登录页 HTML 中找出 era-index-*.js 这类 bundle 的 src。
 * 返回绝对 URL 列表（相对路径基于页面 URL 解析）。
 */
export function findEraBundleUrls(loginPageHtml: string, loginPageUrl: string): string[] {
  const srcs: string[] = [];
  const scriptRe = /<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi;
  let m: RegExpExecArray | null;
  while ((m = scriptRe.exec(loginPageHtml)) !== null) {
    const src = m[1]?.trim();
    if (!src) continue;
    const base = src.replace(/\?.*$/, '').split('/').pop() ?? '';
    if (/^era-index-[\w.-]*\.js$/i.test(base)) {
      try {
        srcs.push(new URL(src, loginPageUrl).toString());
      } catch {
        /* 非法 URL 直接跳过 */
      }
    }
  }
  return srcs;
}

/** 对多条日志消息统一脱敏：截断 token，只保留首尾各 head/tail 个字符。
 *  tail=0 时 `slice(-0)` 会返回完整值（泄密），必须特判为空后缀。 */
export function maskSecret(value: string | undefined | null, head = 4, tail = 4): string {
  if (!value) return '(empty)';
  if (value.length <= head + tail) return '*'.repeat(value.length);
  const suffix = tail > 0 ? value.slice(-tail) : '';
  return `${value.slice(0, head)}…${suffix}`;
}
