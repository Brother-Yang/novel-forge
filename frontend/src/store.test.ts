import { describe, expect, it } from "vitest";
import { useAppStore } from "./store";

describe("useAppStore", () => {
  it("refreshTree 会递增 treeVersion", () => {
    const before = useAppStore.getState().treeVersion;
    useAppStore.getState().refreshTree();
    expect(useAppStore.getState().treeVersion).toBe(before + 1);
  });
});
