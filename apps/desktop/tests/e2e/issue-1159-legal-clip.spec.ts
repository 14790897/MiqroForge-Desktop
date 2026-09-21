/**
 * Regression for #1159：窗口较窄时法律文件正文右侧被裁切。
 *
 * 现象：窗口宽 ≲1520px（issue 实测 1264px）时，正文每行右侧约 1/4 不可见，
 * 且没有横向滚动条；同一次运行放大到 1884px 则完整显示。
 *
 * 根因：设置页 `Tabs.Content`（Tabs.Root 的 flex item）没有归零 `min-width`，
 * `min-width: auto` 把它的下限钉在 min-content 宽度上——《个人信息对外提供清单》
 * 的 `min-w-[720px]` 表格 + `whitespace-nowrap` 表头把 min-content 顶到 ~786px，
 * 于是该栏拒绝收缩到可用宽度（窗口 1264px 时只有 772px），整页被撑到窗口外，
 * 祖先 `overflow-hidden` 再把超出部分裁掉：既不可见也不可滚动。
 * 只给内层补 `min-w-0` 无效——flex item 的 min-content *contribution* 不受它影响，
 * 必须落在「拒绝收缩」的那一层。
 *
 * 修复：给该 Tabs.Content 补 `min-w-0`。正文按可用宽度换行，宽表格回到自己的
 * `overflow-x-auto` 容器里滚动。
 *
 * 断言的是不变量而非像素：正文右缘不得越过窗口右缘，祖先链里任何带非 visible
 * overflow 的容器不得出现横向溢出（scrollWidth > clientWidth 即「被裁掉」）。
 * 修复前窗口 1264px 实测 MAIN.scrollWidth=1226 > clientWidth=1004、正文
 * right=1495 > 窗口 1264。
 *
 * 窗口宽度：应用的默认窗口是 1280 外框 / **1264 内容区**（Windows 边框 16px），
 * 正是 issue 实测的宽度——即默认窗口就落在触发区间里。用例仍显式调窄，
 * 用 setBounds 而不是 setContentSize：后者在 Windows 上会被边框吞掉，
 * 实测设 1264 反而变成 1280（内容区与边框判定被打乱），断言会「空过」。
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

/**
 * 调窗口用的外框宽度：两个值都落在 #1159 的触发区间（内容区 ≲1520px）。
 * 1264 贴近默认窗口（外框 1280 / 内容区 1264），1100 更窄、裁得更多。
 */
const NARROW_WIDTHS = [1264, 1100];

/** 应用允许的最小窗口宽度（src/shared/layout.ts），比它更窄的窗口用户造不出来。 */
const WINDOW_MIN_WIDTH = 900;

/** #1159 的触发阈值：窗口内容区宽 ≳1520px 才完整显示。 */
const TRIGGER_WIDTH = 1520;

/** 带表格的三份文件（表格 min-w-[720px] 是撑宽的源头）。 */
const TABLE_DOCS: Array<[string, string]> = [
  // 6 列表头，min-content 最宽 —— 优先暴露
  ['data-sharing', '第三方服务清单'],
  ['privacy', '设备权限'],
  ['data-collection', '个人信息字段'],
];

/** 把主窗口外框宽度设成指定值（setBounds 才在 Windows 上可靠生效）。 */
async function setWindowWidth(app: ElectronApplication, width: number) {
  await app.evaluate(({ BrowserWindow }, w) => {
    const wins = BrowserWindow.getAllWindows().filter((win) => !win.isDestroyed());
    // 主窗口按标题找；启动画面（480x100）宽度对不上，退路取最宽的那个
    const main =
      wins.find((win) => win.getTitle() === 'MiQroForge Desktop') ??
      wins.sort((a, b) => b.getBounds().width - a.getBounds().width)[0];
    main.setBounds({ width: w, height: 800 });
  }, width);
  // 等重排落地
  await new Promise((r) => setTimeout(r, 400));
}

/** 当前渲染视口宽度（CSS px）。 */
async function viewportWidth(page: Page) {
  return page.evaluate(() => document.documentElement.clientWidth);
}

interface Layout {
  viewportW: number;
  docRight: number | null;
  tableW: number | null;
  tableScrollable: boolean;
  clipped: Array<{ tag: string; cls: string; scrollW: number; clientW: number }>;
}

/** 量正文右缘 + 祖先链横向溢出 + 宽表格是否仍可滚动。 */
async function readLayout(page: Page): Promise<Layout> {
  return page.evaluate(() => {
    const doc = document.querySelector('[data-testid="legal-doc-content"]');
    const pane = document.querySelector('[data-testid="settings-legal-page"]');
    const table = doc?.querySelector('table') ?? null;
    const wrap = (table?.parentElement ?? null) as HTMLElement | null;

    // 从法律文件页往外走：overflow 非 visible 的祖先只要 scrollWidth 超出
    // clientWidth，就说明有内容被它裁掉且没有滚动条可达。
    const clipped: Array<{ tag: string; cls: string; scrollW: number; clientW: number }> = [];
    for (
      let el: HTMLElement | null = pane?.parentElement ?? null;
      el && el !== document.documentElement;
      el = el.parentElement
    ) {
      const cs = getComputedStyle(el);
      if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
      if (el.scrollWidth > el.clientWidth + 1) {
        clipped.push({
          tag: el.tagName,
          cls: el.className,
          scrollW: el.scrollWidth,
          clientW: el.clientWidth,
        });
      }
    }

    const rect = doc?.getBoundingClientRect();
    return {
      viewportW: document.documentElement.clientWidth,
      docRight: rect ? Math.round(rect.right) : null,
      tableW: table ? Math.round(table.getBoundingClientRect().width) : null,
      tableScrollable: !!wrap && wrap.scrollWidth > wrap.clientWidth + 1,
      clipped,
    };
  });
}

test('法律文件正文在窄窗口下不被裁切 (#1159)', { timeout: 300_000 }, async () => {
  const { electronApp, page, miqiHome } = await launchElectronApp();

  try {
    // 应用内入口：设置 → 法律文件
    await page.getByTestId('nav-system-settings').click();
    await page.getByRole('tab', { name: /法律文件/ }).click();
    await expect(page.getByTestId('settings-legal-page')).toBeVisible({ timeout: 30_000 });

    // 先确认「窗口宽度真的能调」：setContentSize 在 Windows 上会被边框吞掉，
    // 静默失败会让下面整组断言在默认宽度上空过。
    await setWindowWidth(electronApp, NARROW_WIDTHS[0]);
    const wide = await viewportWidth(page);
    await setWindowWidth(electronApp, NARROW_WIDTHS[1]);
    const narrow = await viewportWidth(page);
    expect(narrow, '窗口宽度调节未生效（setBounds 没落到主窗口上）').toBeLessThan(wide - 50);

    for (const [docId, marker] of TABLE_DOCS) {
      await page.getByTestId(`legal-nav-${docId}`).click();
      await expect(page.getByTestId('legal-doc-content')).toContainText(marker, {
        timeout: 15_000,
      });

      for (const width of NARROW_WIDTHS) {
        await setWindowWidth(electronApp, width);
        const layout = await readLayout(page);
        const at = `${docId} @ ${width}px（实测视口 ${layout.viewportW}px）`;

        // 前置条件：窗口必须落在 #1159 的触发区间，且不比应用允许的最小窗口更窄
        expect(layout.viewportW, `窗口未落在 #1159 触发区间：${at}`).toBeLessThan(TRIGGER_WIDTH);
        expect(layout.viewportW, `窗口比应用最小宽度还窄：${at}`).toBeGreaterThanOrEqual(
          WINDOW_MIN_WIDTH
        );

        // ① 正文右缘不得越过窗口右缘（修复前 right=1495 > 窗口 1264）
        expect(layout.docRight, `未找到正文容器：${at}`).not.toBeNull();
        expect(layout.docRight!, `正文被窗口右缘裁掉：${at}`).toBeLessThanOrEqual(layout.viewportW);

        // ② 祖先链里不得有「裁掉且不可滚动」的横向溢出
        //    （修复前 MAIN.flex-1.flex.flex-col.overflow-hidden 会进这个列表）
        expect(layout.clipped, `存在被裁掉的横向溢出：${at}`).toEqual([]);

        // ③ 宽表格既没被压扁、也没被藏起来——它应该在自己的容器里横向滚动
        expect(layout.tableW, `宽表格丢失了 min-w-[720px]：${at}`).toBeGreaterThanOrEqual(720);
        expect(layout.tableScrollable, `宽表格无法横向滚动查看：${at}`).toBe(true);
      }
    }

    // 留一张窄窗口下的证据图（PR 用）
    await setWindowWidth(electronApp, NARROW_WIDTHS[0]);
    await page.screenshot({ path: 'test-results/issue-1159-legal-narrow.png' });
  } finally {
    await closeElectronApp(electronApp, miqiHome);
  }
});
