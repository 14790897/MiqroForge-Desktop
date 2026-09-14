/** 资产面板相关的布局常量与纯计算。
 *
 *  放在 shared 是因为 renderer(拖拽 clamp / 面板 CSS min-width)与主进程(窗口最小
 *  宽度按它抬升)必须用同一个值 —— 分散在两处写死迟早漂移(#1047 Review)。 */

/** 资产面板可被压缩到的最小宽度(px)。 */
export const ASSET_PANEL_MIN_WIDTH = 200;

/** 窗口在「面板关闭」状态下的基准最小宽度(与主进程 createWindow 的 minWidth 同源)。 */
export const WINDOW_MIN_WIDTH = 900;

/** 当前窗口宽度是否放得下「面板 + 聊天基准宽度」。
 *
 *  放得下才可以默认展开面板:否则面板一出现就会挤压聊天列(输入框),而冷启动既不该
 *  静默把窗口撑宽(违背 minOnly 的「不改窗口宽」语义),也不该把这个挤压直接呈现给
 *  用户 —— 所以放不下时默认**收起面板**(用户手动展开走交互路径,那条允许撑窗)。
 */
export function canOpenPanelWithoutSqueeze(windowWidth: number): boolean {
  return windowWidth >= WINDOW_MIN_WIDTH + ASSET_PANEL_MIN_WIDTH;
}

/** 面板开/关时,窗口**应保持**的最小宽度(不变量):
 *  面板展开时在基准最小宽度上再加一个面板下限 —— 缩窗时面板先被压到这个下限让位,
 *  聊天列(输入框)因此保住与「面板关闭」时相同的最小宽度。
 *
 *  注意:这里**不做 clamp**。真实窗口可能因为屏幕边缘/最大化而撑不到这个目标,那种
 *  情况下由调用方(主进程)先尝试把窗口撑到目标,撑不动再用 [[clampMinToWindow]] 降级。
 *  若在这里就 clamp,「窗口本来就窄」时返回的值会悄悄放弃不变量(sijie-Z #1047 指出的
 *  900~1099 区间问题),并把降级当成正常结果固化进测试。
 *
 *  @param baseMinWidth 面板未展开时的窗口基准最小宽度
 *  @param panelOpen    面板当前是否展开(含冷启动默认展开)
 */
export function panelWindowMinWidth(baseMinWidth: number, panelOpen: boolean): number {
  return baseMinWidth + (panelOpen ? ASSET_PANEL_MIN_WIDTH : 0);
}

/** 把「目标最小宽度」落到当前实际能达到的宽度上。
 *
 *  只有当窗口确实撑不到目标时(屏幕工作区不够 / 最大化中)才退化:此时 min 取实际宽,
 *  否则 min > 当前窗口宽会让 Windows 把窗口钉死、完全无法再调整大小。#1047 实现过程中
 *  踩过这个坑,所以降级路径必须保留,但要显式、可测、且只作为最后手段。
 */
export function clampMinToWindow(target: number, achievedWidth: number): number {
  return Math.min(target, achievedWidth);
}
