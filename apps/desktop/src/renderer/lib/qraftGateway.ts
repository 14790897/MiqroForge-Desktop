/**
 * Qraft 平台 AI 网关状态展示文案（#922）。
 * QraftPage（平台账号页）与 ModelQuickPanel（模型设置页）共用。
 * Labels follow the active UI language (see ../i18n).
 */

import { i18n } from '../i18n';

export interface GatewayStatusText {
  label: string;
  hint: string;
}

export function gatewayStatusText(status: string): GatewayStatusText {
  switch (status) {
    case 'active':
      return { label: i18n.t('gateway.available'), hint: i18n.t('gateway.availableHint') };
    case 'provisioning':
      return { label: i18n.t('gateway.provisioning'), hint: i18n.t('gateway.provisioningHint') };
    case 'failed':
      return { label: i18n.t('gateway.failed'), hint: i18n.t('gateway.failedHint') };
    case 'disabled':
      return { label: i18n.t('gateway.disabled'), hint: i18n.t('gateway.disabledHint') };
    default:
      return {
        label: status || i18n.t('gateway.unknownLabel'),
        hint: i18n.t('gateway.unknownHint'),
      };
  }
}
