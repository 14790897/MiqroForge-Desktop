/**
 * 录屏：真实跑一次 BVSE 技能（bvse-mof）+ 任务资产面板效果，**全程连续不剪辑**。
 *
 * 录的是真·应用窗口（Electron desktopCapturer + MediaRecorder，窗口流），
 * 不是截图拼帧；中间 8-10 分钟计算过程照录。分片每 15 秒落盘一次，避免长
 * 录像把渲染进程内存撑爆。
 *
 * 前置（同 bvse-skill-assets.spec.ts）：本机装好 skill venv 与测试 CIF：
 *   BVSE_SKILL_DIR 默认 ~/.miqi/skills/bvse-mof-local-ssh
 *   BVSE_TEST_CIF  指向真实 MOF CIF
 *
 * Run:
 *   cd apps/desktop && BVSE_TEST_CIF=<cif> npx playwright test \
 *     --config=playwright.config.ts --project=electron --workers=1 \
 *     record-bvse-skill.spec.ts
 *
 * 产物：`$RECORD_OUT`（默认 <tmp>/miqi-record/bvse-skill-demo.webm），
 * 用 ffmpeg 转 mp4：`ffmpeg -i x.webm -vf scale=trunc(iw/2)*2:trunc(ih/2)*2 \
 *   -c:v libx264 -crf 26 -pix_fmt yuv420p -movflags +faststart x.mp4`
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { appendFileSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import {
  sendMessage,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const SKILL_DIR =
  process.env.BVSE_SKILL_DIR ?? join(homedir(), '.miqi', 'skills', 'bvse-mof-local-ssh');
const SKILL_PY =
  process.platform === 'win32'
    ? join(SKILL_DIR, '.venv', 'Scripts', 'python.exe')
    : join(SKILL_DIR, '.venv', 'bin', 'python');
const TEST_CIF = process.env.BVSE_TEST_CIF ?? '';

const RECORD_DIR = process.env.RECORD_OUT_DIR ?? join(tmpdir(), 'miqi-record');
const RECORD_FILE = join(RECORD_DIR, 'bvse-skill-demo.webm');
const OUT_DIR = join(RECORD_DIR, 'run-output');

/** Drain the renderer-side MediaRecorder chunks and append them to disk. */
async function drainChunks(page: Page, file: string): Promise<number> {
  const parts: string[] = await page.evaluate(async () => {
    const rec = (window as any).__rec;
    if (!rec) return [];
    const blobs: Blob[] = rec.chunks.splice(0);
    const out: string[] = [];
    for (const b of blobs) {
      const buf = new Uint8Array(await b.arrayBuffer());
      let s = '';
      for (let i = 0; i < buf.length; i += 0x8000) {
        s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000) as unknown as number[]);
      }
      out.push(btoa(s));
    }
    return out;
  });
  for (const p of parts) appendFileSync(file, Buffer.from(p, 'base64'));
  return parts.length;
}

async function startRecording(
  electronApp: ElectronApplication,
  page: Page
): Promise<{ surface: string; w: number; h: number; label: string }> {
  // 用「应用主窗口标题」精确匹配捕获源——之前用固定名 'MiQroForge Desktop'
  // 匹配不上（窗口标题带会话名），回退 srcs[0] 录到了别的程序窗口。
  const picked = await electronApp.evaluate(async ({ session, desktopCapturer, BrowserWindow }) => {
    const title = BrowserWindow.getAllWindows()[0]?.getTitle() ?? '';
    let chosen: string | null = null;
    session.defaultSession.setDisplayMediaRequestHandler(async (_req, cb) => {
      const srcs = await desktopCapturer.getSources({ types: ['window'] });
      const win =
        srcs.find((s) => s.name === title) ?? srcs.find((s) => /miqroforge/i.test(s.name)) ?? null;
      if (!win) {
        console.log('[record] 未匹配到应用窗口，可用：' + srcs.map((s) => s.name).join(' | '));
        return cb({});
      }
      chosen = win.name;
      cb({ video: win });
    });
    return title;
  });
  console.log(`[record] 应用窗口标题="${picked}"`);
  const surface = await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    const rec = new MediaRecorder(stream, {
      mimeType: 'video/webm;codecs=vp9',
      videoBitsPerSecond: 1_200_000,
    });
    (window as any).__rec = { rec, chunks: [] as Blob[] };
    rec.ondataavailable = (e: BlobEvent) => {
      if (e.data.size) (window as any).__rec.chunks.push(e.data);
    };
    rec.start(1000);
    const track = stream.getVideoTracks()[0];
    return {
      surface: track.getSettings().displaySurface,
      w: track.getSettings().width,
      h: track.getSettings().height,
      label: track.label,
    };
  });
  console.log(
    `[record] 窗口流: ${surface.surface} ${surface.w}x${surface.h} label="${surface.label}"`
  );
  return surface;
}

async function stopRecording(page: Page, file: string): Promise<void> {
  if (page.isClosed()) return; // 应用已退出：分片已按 10s 间隔落盘
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        const rec = (window as any).__rec;
        if (!rec) return resolve();
        rec.rec.onstop = () => resolve();
        rec.rec.stop();
      })
  );
  await page.waitForTimeout(500);
  await drainChunks(page, file);
}

test.describe('录屏：真实 BVSE 技能 + 任务资产面板（连续）', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );
  test.skip(
    !existsSync(SKILL_PY) || !TEST_CIF || !existsSync(TEST_CIF),
    'BVSE skill venv 或 BVSE_TEST_CIF 缺失——本 spec 只在本机/具备依赖的环境运行'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: ChildProcess;

  test.beforeAll(async () => {
    // 干净的输出目录（pipeline 默认增量，残留会缩短演示）
    rmSync(OUT_DIR, { recursive: true, force: true });
    mkdirSync(RECORD_DIR, { recursive: true });

    const command = [
      `"${SKILL_PY}"`,
      `"${join(SKILL_DIR, 'scripts', 'pipeline.py')}"`,
      `"${TEST_CIF}"`,
      '--ion Na --executor direct --low-e-max 0.5 --skip-zeopp',
      `--out-dir "${OUT_DIR}"`,
    ].join(' ');

    const python =
      process.platform === 'win32'
        ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
        : join(REPO_ROOT, '.venv', 'bin', 'python');
    const port = 20000 + Math.floor(Math.random() * 20000);
    mock = spawn(
      python,
      [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'bvse_skill_mock.py'), String(port)],
      {
        cwd: REPO_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          ...process.env,
          PYTHONUNBUFFERED: '1',
          BVSE_CMD: command,
          BVSE_DECLARE_DIR: OUT_DIR,
        },
        windowsHide: true,
      }
    );
    let url = '';
    mock.stdout?.on('data', (d) => {
      const m = String(d).match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
      if (m) url = `http://127.0.0.1:${m[1]}/v1`;
    });
    const deadline = Date.now() + 30_000;
    while (!url && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250));
    if (!url) throw new Error('bvse mock 未启动');

    const fixture = await launchElectronApp(
      (config: any) => {
        config.providers = config.providers ?? {};
        config.providers.openai = { apiKey: 'mock-key', apiBase: url };
        config.agents = {
          ...(config.agents ?? {}),
          defaults: { ...(config.agents?.defaults ?? {}), model: 'openai/gpt-4o-mini' },
        };
        config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
        // 打开「内联终端输出」（设置 → 通用）：pipeline 跑 9 分钟，默认折叠时
        // 界面全程零可见进度，录屏看着像卡住；打开后 exec 输出会实时流进对话。
        config.desktop = {
          ...(config.desktop ?? {}),
          ui: { ...(config.desktop?.ui ?? {}), inlineExecOutput: true },
        };
      },
      // 录屏必须录到真实窗口画面：默认（本机）窗口停在屏幕外，desktopCapturer
      // 抓这样的窗口可能只有空白/黑屏，这里显式要求窗口正常显示。
      { showWindow: true }
    );
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    // 应用 stderr 落日志——录屏跑长，崩溃时要能看到原因
    electronApp.process().stderr?.on('data', (d) => {
      const s = String(d).trim();
      if (s) console.log(`[app-stderr] ${s.slice(0, 400)}`);
    });
    await waitForBridgeInitialized(page);
    // 窗口缩到 1280 宽：1080p 整窗 VP9 录制太重；取可见窗口（可能有隐藏窗口）
    await electronApp.evaluate(({ BrowserWindow }) => {
      const all = BrowserWindow.getAllWindows();
      const win = all.find((w) => w.isVisible()) ?? all[0];
      if (win) win.setSize(1280, 860);
    });
    await page.waitForTimeout(1500);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mock?.kill();
  });

  test(
    '全程连续录制：发请求 → 真实 pipeline → 面板结果/过程分区与预览',
    { timeout: 25 * 60_000 },
    async () => {
      // ⚠️ describe 层的 test.skip() 会让单测级 timeout 选项失效（实测
      // info.timeout 仍是项目默认 600s）——这里用命令式 API 兜底。
      test.setTimeout(25 * 60_000);
      writeFileSync(RECORD_FILE, Buffer.alloc(0));
      const rec = await startRecording(electronApp, page);
      expect(rec.surface).toBe('window');
      // 录制流必须是应用窗口本身，否则（窗口匹配失败回退）会录到别的程序窗口
      expect(rec.label, `录到了错误的窗口：label="${rec.label}"`).toMatch(/miqroforge/i);

      try {
        // ── ① 发请求（真实用户口吻；命令与输出目录由 mock 环境变量注入）
        const userText =
          `通过本机运行 BVSE 离子迁移筛选：${TEST_CIF} 和 Na 的搭配，low_e_max=0.5 eV，` +
          `输出到 ${OUT_DIR}`;
        await sendMessage(page, userText);
        await page.waitForTimeout(8000);

        // ── ② 等真实 pipeline 跑完（持续落盘分片，8-10 分钟照录）
        const donePath = join(OUT_DIR, 'DONE.json');
        const deadline = Date.now() + 20 * 60_000;
        while (!existsSync(donePath) && Date.now() < deadline) {
          await page.waitForTimeout(10_000);
          if (page.isClosed()) throw new Error('应用在 pipeline 运行期间退出了');
          const n = await drainChunks(page, RECORD_FILE);
          if (n) console.log(`[record] +${n} 分片，等 pipeline…`);
        }
        expect(existsSync(donePath), 'pipeline 未产出 DONE.json').toBe(true);
        await expect(
          page.locator('main').getByText('declared', { exact: false }).first()
        ).toBeVisible({ timeout: 120_000 });
        await page.waitForTimeout(2000);
        await drainChunks(page, RECORD_FILE);

        // ── ③ 面板效果：结果/过程分区 → 批量目录折行 → 差异 → 预览
        const panel = page.getByTestId('task-assets-panel');
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await panel.evaluate((el) => {
          el.scrollTop = 0;
        });
        await page.waitForTimeout(2500); // 停在「结果文件」区

        const processToggle = panel.getByTestId('asset-section-toggle-process');
        const firstProbe = panel.getByText('summary.json', { exact: false }).first();
        if (!(await firstProbe.isVisible().catch(() => false))) {
          await processToggle.click().catch(() => {});
        }
        await page.waitForTimeout(2000);
        await panel.evaluate((el) => {
          el.scrollTop = 220;
        });
        await page.waitForTimeout(2000);

        const sitesGroup = panel
          .getByTestId('asset-dir-group')
          .filter({ hasText: 'bvse_sites/' })
          .first();
        if (await sitesGroup.isVisible().catch(() => false)) {
          await sitesGroup.click().catch(() => {}); // 展开 20 个站点文件
          await page.waitForTimeout(2500);
          await sitesGroup.click().catch(() => {}); // 收起
          await page.waitForTimeout(1500);
        }

        await panel.evaluate((el) => {
          el.scrollTop = 0;
        });
        await page.waitForTimeout(1000);
        const reportCard = panel.locator('.rounded-lg.p-2\\.5').first();
        const diffBtn = reportCard.getByText('差异').first();
        if (await diffBtn.isVisible().catch(() => false)) {
          await diffBtn.click().catch(() => {});
          await page.waitForTimeout(3000);
          await page.keyboard.press('Escape');
          await page.waitForTimeout(1000);
        }
        const previewBtn = reportCard.getByTestId('file-preview-btn');
        if (await previewBtn.isVisible().catch(() => false)) {
          await previewBtn.click().catch(() => {});
          await page.waitForTimeout(5000); // 展示报告正文
          await page.keyboard.press('Escape');
          await page.waitForTimeout(1500);
        }
        await page.waitForTimeout(2000);
      } finally {
        // 停止失败不吞掉主流程异常（分片已按 10s 间隔落盘，视频仍可用）
        await stopRecording(page, RECORD_FILE).catch((e) =>
          console.log(`[record] 停止录制异常：${e}`)
        );
      }

      const { statSync } = await import('node:fs');
      const size = statSync(RECORD_FILE).size;
      console.log(`[record] 完成：${RECORD_FILE}（${(size / 1e6).toFixed(1)} MB）`);
      expect(size).toBeGreaterThan(200_000); // 黑屏/空录会远小于此
    }
  );
});
