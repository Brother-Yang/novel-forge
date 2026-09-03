import { describe, expect, it } from "vitest";
import { taskStageLabel } from "./taskStatus";

describe("taskStageLabel", () => {
  it("从 checkpoint_json 解析阶段文案", () => {
    expect(taskStageLabel('{"stage":"characters"}')).toBe("生成人物");
    expect(taskStageLabel("{}")).toBe("");
    expect(taskStageLabel("not-json")).toBe("");
  });
});
