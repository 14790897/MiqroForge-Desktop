/**
 * 当前活跃 assistant 分组的定位（#843 CodeRabbit：streaming 判定不依赖位置 isLast）。
 *
 * 末尾追加「子代理结果行」「重复 assistant 消息」等分组后，位置上的 last
 * 不再是正在生成的回答——mermaid/svg 会在流式中途丢掉 streaming（源码
 * 预览被提前尝试渲染）。用「最后一条 assistant 分组」判定，追加行不影响。
 */
export interface MinimalChatGroup {
  kind: string;
  msg?: { role?: string };
}

export function lastAssistantGroupIndex(groups: MinimalChatGroup[]): number {
  for (let i = groups.length - 1; i >= 0; i--) {
    const g = groups[i];
    if ((g.kind === 'msg' || g.kind === 'reply-content') && g.msg?.role === 'assistant') {
      return i;
    }
  }
  return -1;
}
