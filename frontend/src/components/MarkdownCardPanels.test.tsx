import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CharacterPanel from "./CharacterPanel";
import WorldbuildingPanel from "./WorldbuildingPanel";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    listCharacters: vi.fn(),
    createCharacter: vi.fn(),
    updateCharacter: vi.fn(),
    deleteCharacter: vi.fn(),
    listWorldbuilding: vi.fn(),
    createWorldbuilding: vi.fn(),
    updateWorldbuilding: vi.fn(),
    deleteWorldbuilding: vi.fn(),
  },
  sseEndpoints: { characters: "/characters", worldbuilding: "/worldbuilding" },
}));
vi.mock("../api/client", () => ({ sseFetch: vi.fn() }));
vi.mock("./MarkdownEditor", () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea
      aria-label="卡片正文"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

beforeEach(() => vi.resetAllMocks());

describe("人物与设定名称保存", () => {
  it.each(["character", "world"])(
    "%s 改名后直接点击保存，慢速名称请求不阻止正文保存",
    async (kind) => {
      const character = {
        id: "card",
        book_id: "book",
        name: "原名称",
        order_index: 0,
        md_content: "原正文",
        state_json: "{}",
      };
      const world = {
        id: "card",
        book_id: "book",
        title: "原名称",
        category: "general",
        md_content: "原正文",
      };
      vi.mocked(api.listCharacters).mockResolvedValue([character]);
      vi.mocked(api.listWorldbuilding).mockResolvedValue([world]);
      let finishName!: () => void;
      const nameSaved = new Promise<void>((resolve) => {
        finishName = resolve;
      });
      vi.mocked(api.updateCharacter).mockImplementation(async (_id, patch) => {
        if ("name" in patch) await nameSaved;
        return { ...character, ...patch };
      });
      vi.mocked(api.updateWorldbuilding).mockImplementation(async (_id, patch) => {
        if ("title" in patch) await nameSaved;
        return { ...world, ...patch };
      });
      render(
        <App>
          {kind === "character" ? (
            <CharacterPanel bookId="book" />
          ) : (
            <WorldbuildingPanel bookId="book" />
          )}
        </App>
      );
      const user = userEvent.setup();
      await user.click(await screen.findByText("原名称"));
      const body = screen.getByRole("textbox", { name: "卡片正文" });
      await user.clear(body);
      await user.type(body, "需要一起保存的新正文");
      const name = screen.getByDisplayValue("原名称");
      await user.clear(name);
      await user.type(name, "新名称");
      // 点击按钮会先触发名称输入框失焦。两个请求都应发生。
      await user.click(
        screen.getByRole("button", { name: kind === "character" ? /保存人物卡$/ : /保存设定$/ })
      );
      const update = kind === "character" ? api.updateCharacter : api.updateWorldbuilding;
      await waitFor(() =>
        expect(update).toHaveBeenCalledWith("card", { md_content: "需要一起保存的新正文" })
      );
      expect(update).toHaveBeenCalledWith(
        "card",
        kind === "character" ? { name: "新名称" } : { title: "新名称" }
      );
      await act(async () => finishName());
      expect(body).toHaveValue("需要一起保存的新正文");
      expect(name).toHaveValue("新名称");
    }
  );

  it.each(["character", "world"])(
    "%s 失焦保存失败可见，名称和正文草稿都保留且可重试",
    async (kind) => {
      const character = {
        id: "card",
        book_id: "book",
        name: "原名称",
        order_index: 0,
        md_content: "原正文",
        state_json: "{}",
      };
      const world = {
        id: "card",
        book_id: "book",
        title: "原名称",
        category: "general",
        md_content: "原正文",
      };
      vi.mocked(api.listCharacters).mockResolvedValue([character]);
      vi.mocked(api.listWorldbuilding).mockResolvedValue([world]);
      vi.mocked(api.updateCharacter)
        .mockRejectedValueOnce(new Error("名称更新失败"))
        .mockResolvedValueOnce({ ...character, name: "新名称" });
      vi.mocked(api.updateWorldbuilding)
        .mockRejectedValueOnce(new Error("名称更新失败"))
        .mockResolvedValueOnce({ ...world, title: "新名称" });
      render(
        <App>
          {kind === "character" ? (
            <CharacterPanel bookId="book" />
          ) : (
            <WorldbuildingPanel bookId="book" />
          )}
        </App>
      );
      const user = userEvent.setup();
      await user.click(await screen.findByText("原名称"));
      const body = screen.getByRole("textbox", { name: "卡片正文" });
      await user.clear(body);
      await user.type(body, "还未保存的正文");
      const name = screen.getByDisplayValue("原名称");
      await user.clear(name);
      await user.type(name, "新名称");
      await user.tab();
      expect(await screen.findByText("名称更新失败")).toBeInTheDocument();
      expect(name).toHaveValue("新名称");
      expect(body).toHaveValue("还未保存的正文");
      await user.click(name);
      await user.tab();
      const update = kind === "character" ? api.updateCharacter : api.updateWorldbuilding;
      await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
      expect(update).toHaveBeenLastCalledWith(
        "card",
        kind === "character" ? { name: "新名称" } : { title: "新名称" }
      );
      expect(name).toHaveValue("新名称");
      expect(body).toHaveValue("还未保存的正文");
    }
  );
});
