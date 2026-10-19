export type CloudConnection = {
  id: string; provider: string; name: string; model: string; base_url: string; proxy: string;
  context_tokens: number; max_output_tokens: number; supports_vision: boolean;
  allow_guests: boolean; allow_images: boolean; has_key?: boolean;
};

export const providerUrls: Record<string, string> = {
  openai: 'https://api.openai.com/v1', deepseek: 'https://api.deepseek.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai', compatible: '',
};

export function modelAccess(owner: boolean, connected: boolean, status: {
  model_phase?: string; cloud?: boolean; cloud_guests_allowed?: boolean;
}) {
  const busy = ['starting', 'stopping', 'loading', 'recovering', 'error'].includes(status.model_phase || '');
  const denied = !owner && status.cloud === true && status.cloud_guests_allowed !== true;
  return { canSend: connected && !busy && !denied, canConfigure: owner && connected && !busy,
    reason: denied ? '主人尚未开放此云模型' : busy ? '模型正在切换或不可用' : '' };
}

export function connectionSubmission(connection: object, key: string) {
  return { type: 'cloud-save', connection, ...(key.trim() ? { api_key: key.trim() } : {}) };
}
