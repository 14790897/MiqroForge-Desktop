/**
 * E2E screenshot → PR comment helper.
 *
 * Uploads a screenshot to the repo's fixed `_gh-imgup` prerelease (release
 * assets API — official, token-only) and posts it as a markdown image into
 * the pull request's comments, so acceptance runs surface their evidence
 * directly in the PR.
 *
 * All API calls use Node's fetch (no shell quoting traps).  Enabled by
 * default on CI; local runs opt in with MIQI_E2E_POST_IMG=1 on a
 * checked-out PR branch (PR number resolved via `gh pr view`).
 */

import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { basename } from 'node:path';

const REPO = process.env.MIQI_IMG_REPO ?? '14790897/MiQi';
const RELEASE_TAG = '_gh-imgup';

function token(): string | null {
  return (
    process.env.GH_TOKEN ??
    process.env.GITHUB_TOKEN ??
    (() => {
      try {
        return execSync('gh auth token', {
          encoding: 'utf8',
          windowsHide: true,
        }).trim();
      } catch {
        return null;
      }
    })()
  );
}

async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const tok = token();
  if (!tok) throw new Error('no GitHub token available');
  const res = await fetch(`https://api.github.com/${path}`, {
    ...init,
    headers: {
      Authorization: `token ${tok}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(
      `GitHub API ${init.method ?? 'GET'} ${path} → ${res.status}: ${await res.text()}`
    );
  }
  return res;
}

function prNumber(): number | null {
  const fromEnv = process.env.GITHUB_EVENT_NUMBER;
  if (fromEnv) {
    const n = Number(fromEnv);
    if (Number.isFinite(n) && n > 0) return n;
  }
  try {
    const out = execSync('gh pr view --json number --jq .number', {
      encoding: 'utf8',
      windowsHide: true,
    });
    const n = Number(out.trim());
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

export function imagePostingEnabled(): boolean {
  if (process.env.MIQI_E2E_POST_IMG === '1') return true;
  if (process.env.MIQI_E2E_POST_IMG === '0') return false;
  return !!process.env.CI;
}

async function ensureImageRelease(): Promise<number> {
  const existing = await api(`repos/${REPO}/releases/tags/${RELEASE_TAG}`, {
    method: 'GET',
  }).catch(() => null);
  if (existing) {
    return (await existing.json()).id as number;
  }
  const created = await api(`repos/${REPO}/releases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tag_name: RELEASE_TAG,
      name: RELEASE_TAG,
      prerelease: true,
      body: 'Image assets - do not delete',
    }),
  });
  return (await created.json()).id as number;
}

async function uploadImage(imagePath: string): Promise<string> {
  const releaseId = await ensureImageRelease();
  const meta = await api(`repos/${REPO}/releases/${releaseId}`);
  const uploadUrlTemplate = (await meta.json()).upload_url as string;
  const uploadUrl = uploadUrlTemplate.replace('{?name,label}', '');
  const stem = basename(imagePath).replace(/\.[^.]+$/, '');
  const ext = basename(imagePath).split('.').pop() ?? 'png';
  // Content hash in-process: shelling out to `sha256sum` breaks on Windows
  // Git Bash with non-ASCII filenames (octal-escaped args → empty hash →
  // mangled asset names like `A-session-.-rm--rf-.-.--.png`).
  const buf = readFileSync(imagePath);
  const hash = createHash('sha256').update(buf).digest('hex').slice(0, 8);
  const name = `${stem}-${hash}.${ext}`;
  const up = await fetch(`${uploadUrl}?name=${name}`, {
    method: 'POST',
    headers: {
      Authorization: `token ${token()}`,
      'Content-Type': 'application/octet-stream',
    },
    body: new Uint8Array(buf),
  });
  if (up.status === 422) {
    // Identical content → identical name → asset already exists.
    // Reuse the existing asset instead of failing.
    const assets = await api(`repos/${REPO}/releases/${releaseId}/assets?per_page=100`);
    const list = (await assets.json()) as Array<{
      name: string;
      browser_download_url: string;
    }>;
    const found = list.find((a) => a.name === name);
    if (found) return found.browser_download_url;
    throw new Error(`asset name conflict for ${name} and reuse failed`);
  }
  if (!up.ok) {
    throw new Error(`asset upload → ${up.status}: ${await up.text()}`);
  }
  const asset = (await up.json()) as { browser_download_url: string };
  return asset.browser_download_url;
}

/** Upload an image and post it into the PR comments. No-op without a PR. */
export async function postScreenshotToPr(imagePath: string, caption: string): Promise<void> {
  if (!imagePostingEnabled()) return;
  const n = prNumber();
  if (n === null) return;
  if (!existsSync(imagePath)) return;

  const url = await uploadImage(imagePath);
  const body = `${caption}\n\n![](${url})`;
  await api(`repos/${REPO}/issues/${n}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  });
}
