/**
 * Qraft 平台 AI 网关状态展示文案（#922）。
 * QraftPage（平台账号页）与 ModelQuickPanel（模型设置页）共用。
 */

export interface GatewayStatusText {
  label: string;
  hint: string;
}

export function gatewayStatusText(status: string): GatewayStatusText {
  switch (status) {
    case 'active':
      return {
        label: '可用',
        hint: 'AI 网关已开通：模型调用将走平台网关，计入平台消费组配额与计费。',
      };
    case 'provisioning':
      return { label: '开通中', hint: 'AI 网关正在开通，暂时无法发起会话。请稍后刷新查看。' };
    case 'failed':
      return { label: '开通失败', hint: 'AI 网关开通失败，请联系平台处理后再试。' };
    case 'disabled':
      return { label: '已停用', hint: 'AI 网关已停用，请联系平台启用后再试。' };
    default:
      return { label: status || '未知', hint: 'AI 网关状态未知，请联系平台确认。' };
  }
}
