/** 后端 http 客户端的报错格式为 `${status}: ${text}`,见 api/client.ts。 */
function isUnsupportedBackendError(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("400");
}

/**
 * 先尝试智能体自主决策生成,若当前 LLM 后端不支持 function calling(返回 400)
 * 则自动降级为固定流水线生成,让用户仍能"一键出书"而不必先弄清楚后端能力。
 * 其他错误(网络错误、409 冲突等)原样抛出。
 */
export async function startAgentWithFallback<T>(
  agentCall: () => Promise<T>,
  fallbackCall: () => Promise<T>
): Promise<{ result: T; usedFallback: boolean }> {
  try {
    return { result: await agentCall(), usedFallback: false };
  } catch (err) {
    if (!isUnsupportedBackendError(err)) throw err;
    return { result: await fallbackCall(), usedFallback: true };
  }
}
