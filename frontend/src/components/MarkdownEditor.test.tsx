import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import MarkdownEditor from "./MarkdownEditor";

function Editor() {
  const [value, setValue] = useState(
    "# 世界观\n\n**城市**与人物\n\n```javascript\nconst hero = '林舟';\n```\n\n```unknown-language\n保留未知代码块原文\n```"
  );
  return <MarkdownEditor value={value} onChange={(next) => setValue(next ?? "")} height={300} />;
}

it("精简编辑器保留编辑、Markdown 预览和代码块内容", async () => {
  render(<Editor />);
  const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
  expect(screen.getByDisplayValue(/世界观/)).toBe(input);
  expect(await screen.findByRole("heading", { name: "世界观" })).toBeInTheDocument();
  expect(screen.getAllByText("保留未知代码块原文").length).toBeGreaterThan(0);
  fireEvent.change(input, { target: { value: "## 新的设定\n\n正文已修改" } });
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "新的设定" })).toBeInTheDocument()
  );
  expect(input).toHaveValue("## 新的设定\n\n正文已修改");
});
