/** 资产面板相关的布局常量与纯计算。
 *
 *  放在 shared 是因为 renderer(拖拽 clamp / 面板 CSS min-width)与主进程(窗口最小
 *  宽度按它抬升)必须用同一个值 —— 分散在两处写死迟早漂移(#1047 Review)。 */

/** 资产面板可被压缩到的最小宽度(px)。 */
export const ASSET_PANEL_MIN_WIDTH = 200;

/** 面板开/关时,窗口应保持的最小宽度。
 *
 *  面板展开时在基准最小宽度上再加一个「面板下限」,这样缩窗时面板先被压到这个下限
 *  让位,聊天列(输入框)保住与「面板关闭」时相同的最小宽度。
 *
 *  **必须 clamp 到不超过当前窗口宽**:若算出的 min ≥ 当前窗口宽,Windows 会把窗口钉死,
 *  用户完全无法再调整大小(#1047 实现过程中踩到过)。
 *
 *  @param baseMinWidth 面板未展开时的窗口基准最小宽度
 *  @param panelOpen    面板当前是否展开(含冷启动默认展开)
 *  @param currentWidth 当前窗口宽度
 */
export function panelWindowMinWidth(
  baseMinWidth: number,
  panelOpen: boolean,
  currentWidth: number
): number {
  const want = baseMinWidth + (panelOpen ? ASSET_PANEL_MIN_WIDTH : 0);
  return Math.min(want, currentWidth);
}
