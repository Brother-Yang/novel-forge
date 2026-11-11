import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { BookTree } from "../api/types";
import CatalogTree from "./CatalogTree";

vi.mock("../api", () => ({
  api: {
    getTree: vi.fn(),
    deleteVolume: vi.fn(),
    deleteChapter: vi.fn(),
  },
}));

const chapter = {
  id: "chapter-1",
  title: "第一章",
  order_index: 0,
  status: "done",
  word_count: 2000,
};
const tree: BookTree = {
  volumes: [{ id: "volume-1", title: "第一卷", order_index: 0, chapters: [chapter] }],
  loose_chapters: [],
};

function Harness() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <ConfigProvider>
      <AntdApp>
        <CatalogTree bookId="book-1" selectedChapter={selected} onSelectChapter={setSelected} />
        <output aria-label="当前章节">{selected ?? "未选择"}</output>
      </AntdApp>
    </ConfigProvider>
  );
}

async function openDeleteMenu(index = 0) {
  const user = userEvent.setup();
  await screen.findByText("第一章");
  await user.click(screen.getAllByRole("img", { name: "more" })[index]);
  await user.click(await screen.findByRole("menuitem", { name: "删除" }));
  return user;
}

describe("CatalogTree 删除保护", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getTree).mockResolvedValue(tree);
    vi.mocked(api.deleteVolume).mockResolvedValue({ ok: true });
    vi.mocked(api.deleteChapter).mockResolvedValue({ ok: true });
  });

  it("删除卷前说明章节会保留，取消时不发删除请求", async () => {
    render(<Harness />);
    const user = await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/章节及正文会保留并移到“未归卷”/)).toBeInTheDocument();
    expect(api.deleteVolume).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: /取消|取 消/ }));
    expect(api.deleteVolume).not.toHaveBeenCalled();
    expect(screen.getByText("第一章")).toBeInTheDocument();
  });

  it("确认删除卷后重新加载目录并保留当前章节选择", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(await screen.findByText("第一章"));
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [chapter] });
    await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除卷" }));
    await waitFor(() => expect(api.deleteVolume).toHaveBeenCalledWith("volume-1"));
    await waitFor(() => expect(screen.queryByText("第一卷")).not.toBeInTheDocument());
    expect(screen.getByText("第一章")).toBeInTheDocument();
    expect(screen.getByLabelText("当前章节")).toHaveTextContent("chapter-1");
  });

  it("删除失败会提示错误并保留确认框，允许重试", async () => {
    vi.mocked(api.deleteVolume).mockRejectedValueOnce(new Error("删除失败，请重试"));
    render(<Harness />);
    const user = await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除卷" }));
    expect(await screen.findByText("删除失败，请重试")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("第一章")).toBeInTheDocument();
    const retry = within(dialog).getByRole("button", { name: /删除卷$/ });
    await waitFor(() => expect(retry).not.toHaveClass("ant-btn-loading"));
    await user.click(retry);
    await waitFor(() => expect(api.deleteVolume).toHaveBeenCalledTimes(2));
  });

  it("选择章节后从目录菜单删除该章，确认后清除当前选择", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(await screen.findByText("第一章"));
    await openDeleteMenu(1);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/正文和摘要/)).toBeInTheDocument();
    expect(api.deleteChapter).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "删除章节" }));
    await waitFor(() => expect(api.deleteChapter).toHaveBeenCalledWith("chapter-1"));
    expect(screen.getByLabelText("当前章节")).toHaveTextContent("未选择");
  });
});
