/**
 * 极简 cookie jar — MiQroForge 平台登录态依赖 cookie 传递：
 * 服务端在登录响应下发 `Set-Cookie: Authorization=<uuid>; Path=/`，
 * 后续 OAuth 授权流程必须携带该 cookie（实测用 header 不生效）。
 *
 * 只维护 name=value 映射，不处理 domain/path/expires 语义
 * （客户端只连同一个 origin，复杂度不需要）。
 */

export interface CookieJarLike {
  storeFromResponse(res: { headers: HeadersLike }): void;
  get(name: string): string | undefined;
  header(): string;
  clear(): void;
  isEmpty(): boolean;
}

/** Headers 的最小结构，便于测试注入。 */
export interface HeadersLike {
  get(name: string): string | null;
  getSetCookie?: () => string[];
  raw?: () => Record<string, string[]>;
}

/** 从一条 Set-Cookie 头解析出 name=value（失败返回 null）。 */
export function parseSetCookieValue(header: string): { name: string; value: string } | null {
  const firstPart = header.split(';')[0]?.trim() ?? '';
  const eq = firstPart.indexOf('=');
  if (eq <= 0) return null;
  return { name: firstPart.slice(0, eq).trim(), value: firstPart.slice(eq + 1).trim() };
}

/**
 * 从 Response 收集全部 Set-Cookie 头。
 *
 * fetch 规范的 getSetCookie() 可完整取到多条头；旧实现没有该 API 时退化为
 * 单条 get('set-cookie') 并按逗号拆分 —— 拆分只在片段以 "name=" 开头时成立，
 * 以免把 `Expires=Wed, 21 Oct ...` 里的逗号误当成分隔符。
 */
export function collectSetCookieHeaders(headers: HeadersLike): string[] {
  if (typeof headers.getSetCookie === 'function') {
    try {
      const list = headers.getSetCookie();
      if (list.length > 0) return list;
    } catch {
      /* fall through to raw/get */
    }
  }
  const raw = headers.raw?.()?.['set-cookie'] ?? headers.raw?.()?.['Set-Cookie'];
  if (raw && raw.length > 0) return raw;
  const single = headers.get('set-cookie');
  if (!single) return [];
  // 只按 "name=" 起始位置切分，Expires 内部的逗号不会匹配该模式。
  return single
    .split(/,(?=\s*[\w-]+=)/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export class CookieJar implements CookieJarLike {
  private cookies = new Map<string, string>();

  storeFromResponse(res: { headers: HeadersLike }): void {
    for (const header of collectSetCookieHeaders(res.headers)) {
      const parsed = parseSetCookieValue(header);
      if (!parsed) continue;
      // 值为空或 "deleted"/过期（Max-Age<=0，含 -1）表示删除 —— MiQroForge 场景里直接清掉。
      if (
        parsed.value === '' ||
        /^delete/i.test(parsed.value) ||
        /max-age\s*=\s*(0|-\d+)/i.test(header)
      ) {
        this.cookies.delete(parsed.name);
        continue;
      }
      this.cookies.set(parsed.name, parsed.value);
    }
  }

  /** 直接写入一对 cookie（用于从持久化状态恢复）。 */
  set(name: string, value: string): void {
    this.cookies.set(name, value);
  }

  get(name: string): string | undefined {
    return this.cookies.get(name);
  }

  header(): string {
    return [...this.cookies.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
  }

  clear(): void {
    this.cookies.clear();
  }

  isEmpty(): boolean {
    return this.cookies.size === 0;
  }
}
