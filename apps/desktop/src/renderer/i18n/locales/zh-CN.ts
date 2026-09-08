/**
 * zh-CN copy — canonical source of truth for the UI strings migrated in the
 * i18n pilot. Values MUST stay byte-identical to the pre-i18n hardcoded copy
 * (tests/E2E assert on these texts).
 *
 * Key naming: `<area>.<name>` where area is the owning page/component
 * (topbar./feedback./relative./common.). Keep zh keys sorted roughly by area.
 */
export const zhCN = {
  // ── shared bits ────────────────────────────────────────────────────────────
  'common.cancel': '取消',
  'common.retry': '重试',
  'common.refresh': '刷新',
  'common.listSeparator': '、',

  // ── TopBar ─────────────────────────────────────────────────────────────────
  'topbar.agentLabel': 'MiQroForge 智能体',
  // approval bypass capsule
  'topbar.bypass.labelAuto': '自动',
  'topbar.bypass.labelAll': '全部绕过',
  'topbar.bypass.labelBypass': '绕过',
  'topbar.bypass.titleAuto': '自动模式：所有审批已绕过',
  'topbar.bypass.titleAll': '所有审批类别已启用绕过',
  'topbar.bypass.titleNone': '打开审批设置',
  'topbar.bypass.joined': '已绕过: {{list}}',
  'topbar.bypass.itemCommand': '命令审批',
  'topbar.bypass.itemFileWrite': '文件写入审批',
  'topbar.bypass.itemTool': '工具确认',
  'topbar.bypass.itemNetwork': '网络审批',
  'topbar.bypass.detailAuto': '自动模式',
  'topbar.bypass.detailAll': '全部操作',
  'topbar.bypass.detailCommand': '命令执行',
  'topbar.bypass.detailFileWrite': '文件写入',
  'topbar.bypass.detailTool': '工具调用',
  'topbar.bypass.detailNetwork': '网络请求',
  'topbar.bypass.detailSeparator': ' · ',
  // runtime status capsule
  'topbar.runtime.connected': '运行时已连接',
  'topbar.runtime.startingDots': '正在启动/停止运行时…',
  'topbar.runtime.offlineTitle': '运行时未连接，点击重新连接',
  'topbar.runtime.starting': '正在启动/停止运行时',
  'topbar.runtime.synced': '已同步',
  'topbar.runtime.syncing': '同步中',
  'topbar.runtime.offline': '离线',

  // ── Feedback page ──────────────────────────────────────────────────────────
  'feedback.title': '用户反馈',
  'feedback.submit': '提交反馈',
  'feedback.submitFirst': '提交第一条反馈',
  'feedback.empty': '暂无反馈记录',
  'feedback.emptyHint': '提交反馈将自动附加日志并发送到飞书',
  'feedback.loadError': '加载反馈记录失败',
  'feedback.catLabel': '类别',
  'feedback.catCard.bug': '🐛 缺陷报告',
  'feedback.catCard.question': '❓ 使用问题',
  'feedback.catCard.suggestion': '💡 功能建议',
  'feedback.catCard.other': '📝 其他',
  'feedback.catName.bug': '缺陷报告',
  'feedback.catName.question': '使用问题',
  'feedback.catName.suggestion': '功能建议',
  'feedback.catName.other': '其他',
  'feedback.titleLabel': '标题',
  'feedback.titlePlaceholder': '简要描述你的问题或建议',
  'feedback.contentLabel': '详细描述',
  'feedback.contentPlaceholder': '请详细描述你的问题或建议...',
  'feedback.contactLabel': '联系方式（选填）',
  'feedback.contactPlaceholder': '邮箱或飞书账号，方便我们联系你',
  'feedback.screenshotLabel': '截图（选填，可拖入 / 粘贴 / 点击上传）',
  'feedback.dropHint': '拖入图片 / 粘贴 (Ctrl+V) / 点击选择',
  'feedback.supportedFormats': '支持 PNG / JPG / GIF / WebP，单张 ≤ 10MB',
  'feedback.remove': '移除',
  'feedback.submitting': '提交中...',
  'feedback.successTitle': '提交成功！',
  'feedback.successHint': '日志已自动附加并发送到飞书',
  'feedback.hintAutoAttach': '日志将在提交时自动附加并发送到飞书',
  'feedback.hintCopyFirst': '提示：建议先复制已填写的提示词，避免因意外关闭而丢失',
  'feedback.confirmDiscard': '放弃已填写的内容？',
  'feedback.errUnsupportedType': '不支持的文件类型: {{type}}',
  'feedback.errUnknownType': '未知',
  'feedback.errTooLarge': '{{name}} 超过 10MB 限制',
  'feedback.errReadFile': '读取文件失败',
  'feedback.errRejected': '{{count}} 个文件未添加（不支持的类型或超过 10MB）',
  'feedback.errMax': '最多 {{count}} 张截图',
  'feedback.errPartial': '仅添加了前 {{count}} 张，已达 {{max}} 张上限',
  'feedback.errProcess': '处理图片失败',
  'feedback.errSubmitUnconfirmed': '提交未确认（后端返回 ok=false）',
  'feedback.errSubmit': '提交失败，请重试',

  // ── Relative/absolute time (shared lib/formatTime) ─────────────────────────
  'relative.justNow': '刚刚',
  'relative.yesterday': '昨天',
  // zh-CLDR has no one/other split, so both keys render the same text; en uses
  // the _one/_other keys for "1 minute ago" vs "N minutes ago".
  'relative.minutesAgo_one': '{{count}} 分钟前',
  'relative.minutesAgo_other': '{{count}} 分钟前',
  'relative.hoursAgo_one': '{{count}} 小时前',
  'relative.hoursAgo_other': '{{count}} 小时前',
} as const;

export type I18nKey = keyof typeof zhCN;
