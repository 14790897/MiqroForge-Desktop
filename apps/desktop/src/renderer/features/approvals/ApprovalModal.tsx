import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Terminal, FileText, AlertTriangle, X } from 'lucide-react';
import { useApproval } from '../../contexts/ApprovalContext';
import { cn } from '../../lib/utils';
import type { PendingApproval } from '../../../shared/ipc';
import { getApprovalDisplay, getApprovalTitle } from './approvalDisplayUtils';

type Decision = 'once' | 'session' | 'always' | 'deny';

/**
 * ApprovalModal — 阻塞式审批（命令 / 文件写入 / 其他工具调用）。
 *
 * 形态对齐 WorkBuddy 的 sandbox-intercept-card（它源码就在
 * packages/cb-chat-ui/src/components/chat-input/ 下）：卡片**沾满输入框宽度、位于
 * 输入框内部**，靠输入框自身被撑高把消息区顶上去，而不是浮在窗口中央的模态。
 * 位置/宽度的实现方式沿用本仓库已有的「框内插槽 + portal」模式（附件预览就是
 * 这么投进 Composer 的 `<div data-testid="approval-slot">`）。
 *
 * 兜底：审批是全局的（任何页面都可能弹），而插槽只存在于聊天页。拿不到插槽
 * （用户在设置/审批页等）时退回居中模态 + 遮罩，保证审批永远不会被漏掉。
 *
 * 列表几何按 WorkBuddy 真机实测值定：行高 40 / 序号徽章 24×24（圆角 7）/ 徽章到
 * 标签 8px。**一律写 px 不写 rem** —— MiQi 根字号是 14px，h-10/size-6 这类会缩成
 * 35/21px，对不上参考。
 */
export function ApprovalModal() {
  const { pending, resolve, remainingSeconds } = useApproval();

  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const resolveRef = useRef(resolve);
  resolveRef.current = resolve;

  // 输入框内插槽（聊天页才有）。审批期间监听 DOM 变化，因为切页面会挂/卸 Composer。
  // 但「插槽存在」不等于「插槽可见」：App.tsx 切到设置等页面时只是给聊天区加
  // `hidden`、并**不卸载** ChatConsole，插槽元素仍在 DOM 里。portal 进一个
  // display:none 的容器 = 审批卡彻底不可见、turn 卡到超时，所以必须确认插槽真的
  // 被渲染（offsetParent 非 null）。拿不到就退回居中模态 —— 模态是 fixed 定位，
  // 在任何页面都可见。
  //
  // 还要盯住插槽**祖先链**的 class/style：页面切换就是给祖先换 className，只观察
  // childList 的话「审批挂着时用户切走」不会重算，卡片会跟着聊天区一起消失。
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!pending) {
      setSlot(null);
      return;
    }
    let observedSlot: HTMLElement | null = null;
    let observer: MutationObserver | null = null;

    function sync() {
      const el = document.querySelector<HTMLElement>('[data-testid="approval-slot"]');
      setSlot(el && el.offsetParent !== null ? el : null);
      if (el === observedSlot) return;
      observedSlot = el;
      // MutationObserver 没有 unobserve（只能整个 disconnect），所以换目标时重挂
      observer?.disconnect();
      observer?.observe(document.body, { childList: true, subtree: true });
      for (let n = el?.parentElement; n && n !== document.body; n = n.parentElement) {
        observer?.observe(n, { attributes: true, attributeFilter: ['class', 'style'] });
      }
    }

    observer = new MutationObserver(sync);
    observer.observe(document.body, { childList: true, subtree: true });
    sync();
    return () => observer?.disconnect();
  }, [pending]);

  // Esc → 拒绝；⏎ → 允许一次。让位规则只看**当前真正可见、可编辑**的元素：
  // 审批挂起时 Composer 用祖先 display:none 把输入内容藏起来，textarea 自己的
  // 标签仍是 TEXTAREA（offsetParent 为 null），此时必须接管键盘，否则快捷键全废。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!pendingRef.current) return;
      const active = document.activeElement as HTMLElement | null;
      const editable =
        !!active &&
        (active.tagName === 'TEXTAREA' ||
          active.tagName === 'INPUT' ||
          active.isContentEditable === true) &&
        active.offsetParent !== null;
      if (editable) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        void resolveRef.current('deny');
      } else if (e.key === 'Enter' && !e.shiftKey) {
        // 焦点在按钮上时让按钮自己响应 Enter（避免双触发）
        if (active?.tagName === 'BUTTON') return;
        e.preventDefault();
        void resolveRef.current('once');
      }
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, []);

  if (!pending) return null;

  const card = (
    <ApprovalCard
      pending={pending}
      resolve={resolve}
      remainingSeconds={remainingSeconds}
      variant={slot ? 'inline' : 'modal'}
    />
  );

  if (slot) return createPortal(card, slot);

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/30 px-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="approval-title"
        className="relative w-full max-w-[440px] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4 shadow-xl"
      >
        {card}
      </div>
    </div>
  );
}

function ApprovalCard({
  pending,
  resolve,
  remainingSeconds,
  variant,
}: {
  pending: PendingApproval;
  resolve: (d: Decision) => Promise<void>;
  remainingSeconds: number | null;
  variant: 'inline' | 'modal';
}) {
  const inline = variant === 'inline';
  // 审批出现时把焦点收到卡片上。不这么做的话焦点会留在上次交互的元素上——
  // 实测应用启动后焦点停在侧栏的圆形按钮上，于是 Enter 会去点那个无关按钮、
  // Esc 也会被"输入类元素让位"的规则吞掉。聚焦到卡片后两个键的语义才唯一。
  // 代价是这里会出现聚焦环：globals.css 的 `:focus-visible:not(input):not(textarea)…`
  // 给一切非输入元素画 2px `--accent`（橙）outline，而它对 div 生效、特异性又高于
  // 普通工具类，所以必须 `!`。根节点是 tabIndex=-1、本就不在 Tab 序列里，去掉环
  // 不损失键盘可达性。
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = rootRef.current;
    const prev = document.activeElement as HTMLElement | null;
    root?.focus();
    return () => {
      // 审批结束后把焦点还回去，否则键盘流断掉（批准后还得先点一下才能打字）。
      // 内联形态归还给**输入框**——卡片顶掉的就是它，⏎ 批准后直接接着打字最顺；
      // 实测卡片挂载时 activeElement 已经是被 blur 掉的 body（发消息后就如此），
      // 所以"还给打开前的元素"在内联形态下等于什么都没做。
      // 兜底形态才还给打开前的元素：那时聊天区仍可能是 hidden，把焦点丢进一个
      // 不可见的 textarea 只会让人困惑。
      // 只在焦点仍由本卡持有时归还——用户已经点到别处了就别抢回来。
      const active = document.activeElement;
      if (!root || (active !== root && active !== document.body)) return;
      const target = inline
        ? document.querySelector<HTMLElement>('[data-testid="chat-input-container"] textarea')
        : prev;
      if (target?.isConnected) target.focus();
    };
  }, []);
  // 兜底模态把 Tab 圈在卡内。`aria-modal` 只声明语义、不拦键盘：不拦的话 Tab 会走到
  // 遮罩后面的按钮上——那些按钮用户看不见，却能按回车点中。内联形态不需要（它就是
  // 输入框的一部分，没有"后面"）。
  const onKeyDownTrap = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (inline || e.key !== 'Tab') return;
    const root = rootRef.current;
    const list = Array.from(
      root?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ) ?? []
    );
    if (list.length === 0) return;
    const first = list[0];
    const last = list[list.length - 1];
    const active = document.activeElement;
    const outside = !root || !root.contains(active) || active === root;
    if (e.shiftKey ? outside || active === first : outside || active === last) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    }
  };
  const title = getApprovalTitle(pending.category);
  const display = getApprovalDisplay(pending);
  const description = (pending.description ?? '').trim();
  const descRedundant =
    !description ||
    description === display ||
    description.endsWith(display) ||
    display.includes(description);

  const isLow = remainingSeconds != null && remainingSeconds <= 5;

  const options: Array<{ id: Decision; label: string; testid: string; hint?: string }> = [
    { id: 'once', label: '允许一次', testid: 'approval-allow-once', hint: '⏎' },
    { id: 'session', label: '本次会话允许', testid: 'approval-allow-session' },
  ];
  if (pending.allow_permanent) {
    options.push({ id: 'always', label: '永久允许', testid: 'approval-allow-permanent' });
  }
  options.push({ id: 'deny', label: '拒绝', testid: 'approval-deny', hint: 'Esc' });

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      role={inline ? 'group' : undefined}
      aria-labelledby={inline ? 'approval-title' : undefined}
      onKeyDown={onKeyDownTrap}
      className={cn('outline-none!', inline && 'flex flex-col')}
    >
      {/* 标题行：图标 + 标题 …… 剩余秒数 + 关闭（关闭 = 拒绝）。
          倒计时只留文本、不做进度条（用户定稿：不要那条黄条）；秒数归到最右，
          它是状态不是标题的一部分。 */}
      <div className="flex items-center gap-2 pb-1.5">
        <ApprovalIcon category={pending.category} />
        <span
          id="approval-title"
          data-testid="approval-title"
          className="min-w-0 truncate text-[15px] font-semibold text-[var(--text)]"
        >
          {title}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {remainingSeconds != null && (
            <span
              className="font-mono text-[12px] tabular-nums"
              style={{ color: isLow ? 'var(--danger)' : 'var(--text-faint)' }}
            >
              {remainingSeconds > 0 ? `${remainingSeconds}秒` : '已超时'}
            </span>
          )}
          <button
            onClick={() => void resolve('deny')}
            aria-label="拒绝"
            className="shrink-0 text-[var(--text-faint)] transition-colors hover:text-[var(--text)]"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* 命令：单行省略、hover 看全文（对齐参考的 commandInline）。
          不加边框、不等高块——它是这一行里唯一需要读的东西。 */}
      {!descRedundant && (
        <div className="truncate pb-1 text-[13px] leading-normal text-[var(--text-muted)]">
          {description}
        </div>
      )}
      {/* 命令：最多两行、hover 看全文。参考是单行省略，但这里命令是判断依据，
          恰好一行放不下的命令（本次实测 76 字符就会超）不该把尾巴藏掉；
          短命令仍然只有一行，不增加高度。 */}
      <div
        title={display}
        className="mb-1.5 line-clamp-2 rounded-[8px] bg-[var(--surface-muted)] px-2.5 py-1.5 font-mono text-[13px] leading-[1.5] break-all text-[var(--text)]"
      >
        {display}
      </div>

      {/* 选项：一行四档，拒绝在最右。横排后不再需要序号徽章——那套是为竖排列表
          服务的（且会暗示 1-4 快捷键，而内联形态不接管键盘）。键位提示只在兜底
          模态里显示，那里快捷键真的生效。 */}
      <div className="flex items-stretch gap-2">
        {options.map((o) => (
          <button
            key={o.id}
            data-testid={o.testid}
            onClick={() => void resolve(o.id)}
            className={cn(
              'flex h-[34px] min-w-0 flex-1 items-center justify-center gap-1.5 rounded-[8px] px-2 text-[13.5px] font-medium transition-colors',
              o.id === 'once' &&
                'bg-[var(--accent)] text-[var(--accent-text)] hover:bg-[var(--accent-hover)]',
              o.id === 'deny' &&
                'bg-[color-mix(in_srgb,var(--danger)_10%,transparent)] text-[var(--danger)] hover:bg-[color-mix(in_srgb,var(--danger)_18%,transparent)]',
              o.id !== 'once' &&
                o.id !== 'deny' &&
                'border border-[var(--border-subtle)] bg-[var(--surface-muted)] text-[var(--text)] hover:bg-[var(--surface-hover)]'
            )}
          >
            <span className="truncate">{o.label}</span>
            {o.hint && (
              <span className="shrink-0 font-mono text-[10.5px] opacity-55">{o.hint}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function ApprovalIcon({ category }: { category?: string }) {
  const cls = 'shrink-0 text-[var(--text-muted)]';
  if (category === 'exec') return <Terminal size={15} className={cls} />;
  if (category === 'file_write') return <FileText size={15} className={cls} />;
  if (category === 'patch_apply') return <FileText size={15} className={cls} />;
  return <AlertTriangle size={15} className={cls} />;
}
