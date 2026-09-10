import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { copySvgAsPng, downloadSvgAsPng } from '../../../lib/svgImage';
import { DiagramViewer, type GalleryFig } from './DiagramViewer';

/**
 * 图集 Gallery（issue #671 QQ 邮箱看图语言）。
 * 同一消息（一个 MarkdownContent 实例）内的所有流程图/svg 图注册到
 * provider：点击任一卡 → 打开图集查看器，多图可 ←/→、胶片切换。
 * 复制/下载动作统一走 svgImage（copySvgAsPng/downloadSvgAsPng）。
 *
 * 稳定 id 约定：注册方（DiagramCard）自持一次生成的 id，内容变化走
 * upsert(id, fig)；**禁止** unregister+register 换 id——否则 provider
 * value（含 figs）变化会触发卡 effect 重跑 → 再换 id → 无限循环（#843
 * E2E 实测胶片顺序错乱）。
 */
interface GalleryCtx {
  figs: GalleryFig[];
  /** 注册/更新一张图（同 id 幂等：内容无变化不触发 state 变更） */
  upsert: (id: string, fig: Omit<GalleryFig, 'id'>) => void;
  /** 卸载一张图（组件卸载时调用） */
  unregister: (id: string) => void;
  /** 打开以 id 命中的图的查看器 */
  open: (id: string) => void;
}

const Ctx = createContext<GalleryCtx | null>(null);

/** 在 DiagramGalleryProvider 内取图集能力；provider 外为 null（调用方兜底） */
export function useDiagramGallery(): GalleryCtx | null {
  return useContext(Ctx);
}

/** 生成一次性的稳定注册 id（模块级，跨 provider 实例唯一） */
export function newDiagramId(): string {
  return `diag-${++uid}`;
}

let uid = 0;

export function DiagramGalleryProvider({ children }: { children: ReactNode }) {
  const [figs, setFigs] = useState<GalleryFig[]>([]);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  // figs 最新值镜像（open 用 ref 读取，保持 open 的稳定 identity）
  const figsRef = useRef<GalleryFig[]>([]);
  useEffect(() => {
    figsRef.current = figs;
  }, [figs]);

  const upsert = useCallback((id: string, fig: Omit<GalleryFig, 'id'>) => {
    setFigs((prev) => {
      const idx = prev.findIndex((f) => f.id === id);
      if (idx >= 0) {
        const cur = prev[idx];
        if (cur.svg === fig.svg && cur.label === fig.label) return prev; // 无变化
        const next = prev.slice();
        next[idx] = { ...fig, id };
        return next;
      }
      return [...prev, { ...fig, id }];
    });
  }, []);

  const unregister = useCallback((id: string) => {
    setFigs((prev) => {
      const next = prev.filter((f) => f.id !== id);
      return next.length === prev.length ? prev : next;
    });
  }, []);

  const open = useCallback((id: string) => {
    // 经 ref 读最新 figs——不在 setFigs updater 内触发另一个 setState
    // （外部分析：state updater 内二次 setState 在 concurrent/StrictMode
    // 下不可取）
    const i = figsRef.current.findIndex((f) => f.id === id);
    if (i >= 0) setViewerIndex(i);
  }, []);

  const value = useMemo<GalleryCtx>(
    () => ({ figs, upsert, unregister, open }),
    [figs, upsert, unregister, open]
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {viewerIndex !== null && figs.length > 0 && (
        <DiagramViewer
          figs={figs}
          index={Math.min(viewerIndex, figs.length - 1)}
          onSelect={setViewerIndex}
          onClose={() => setViewerIndex(null)}
          onCopy={async (svg) => copySvgAsPng(svg)}
          onDownload={async (svg) => downloadSvgAsPng(svg, `diagram-${Date.now()}.png`)}
        />
      )}
    </Ctx.Provider>
  );
}

export type { GalleryFig };
