import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CharacterFacts from "./CharacterFacts";

describe("人物正文事实", () => {
  it("按明确字段显示正文状态，并说明作者人物卡仍保留", () => {
    render(
      <CharacterFacts state={{ location: "北堤仓库", condition: "左手受伤", goal: "送信给医师" }} />
    );
    expect(screen.getByText("北堤仓库")).toBeInTheDocument();
    expect(screen.getByText("左手受伤")).toBeInTheDocument();
    expect(screen.getByText("送信给医师")).toBeInTheDocument();
    expect(screen.queryByText("关系变化")).not.toBeInTheDocument();
    expect(screen.getByText(/人物卡保留作者设定/)).toBeInTheDocument();
  });

  it("修订使事实失效时移除旧值并显示未同步状态", () => {
    const view = render(<CharacterFacts state={{ location: "旧仓库" }} />);
    view.rerender(<CharacterFacts state={{}} />);
    expect(screen.queryByText("旧仓库")).not.toBeInTheDocument();
    expect(screen.getByText("尚无已同步的正文事实。")).toBeInTheDocument();
  });
});
