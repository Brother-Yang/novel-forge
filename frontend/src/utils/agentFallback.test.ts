import { describe, expect, it, vi } from "vitest";
import { startAgentWithFallback } from "./agentFallback";

describe("startAgentWithFallback", () => {
  it("agent 调用成功时直接返回结果,不降级", async () => {
    const agentCall = vi.fn().mockResolvedValue("agent-ok");
    const fallbackCall = vi.fn();
    const { result, usedFallback } = await startAgentWithFallback(agentCall, fallbackCall);
    expect(result).toBe("agent-ok");
    expect(usedFallback).toBe(false);
    expect(fallbackCall).not.toHaveBeenCalled();
  });

  it("400 错误(后端不支持自主决策)时降级到固定流程", async () => {
    const agentCall = vi.fn().mockRejectedValue(new Error("400: 当前 LLM 后端不支持自主决策"));
    const fallbackCall = vi.fn().mockResolvedValue("fallback-ok");
    const { result, usedFallback } = await startAgentWithFallback(agentCall, fallbackCall);
    expect(result).toBe("fallback-ok");
    expect(usedFallback).toBe(true);
  });

  it("非 400 错误直接抛出,不降级", async () => {
    const agentCall = vi.fn().mockRejectedValue(new Error("409: 已有任务在跑"));
    const fallbackCall = vi.fn();
    await expect(startAgentWithFallback(agentCall, fallbackCall)).rejects.toThrow("409");
    expect(fallbackCall).not.toHaveBeenCalled();
  });
});
