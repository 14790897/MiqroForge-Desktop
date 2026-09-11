/**
 * MiQroForge 官方品牌 Logo（#828）。
 *
 * 使用 docs/brand/ 官方素材（量子/卡片/节点/连接）:
 * - 浅色主题 → 标志2（彩色版）
 * - 暗色主题 → 标志1（白色版）
 *
 * 通过纯 CSS 类切换（.dark），无 JS 状态依赖。
 */
import logoIconLight from '../assets/brand/logo-icon-light.png';
import logoIconDark from '../assets/brand/logo-icon-dark.png';

export function MiQroForgeLogo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center shrink-0">
      <img
        src={logoIconLight}
        alt="MiQroForge"
        style={{ width: size, height: size, objectFit: 'contain' }}
        className="logo-theme-light"
      />
      <img
        src={logoIconDark}
        alt="MiQroForge"
        style={{ width: size, height: size, objectFit: 'contain' }}
        className="logo-theme-dark"
      />
    </span>
  );
}
