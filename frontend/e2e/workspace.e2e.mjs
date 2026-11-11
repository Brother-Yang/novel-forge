import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { URL } from "node:url";
import { expect, test } from "./fixtures.mjs";

async function login(page, runtime, { checkWrongPassword = false } = {}) {
  await page.goto(runtime.origin);
  await expect(page.getByText("登录小说工作台", { exact: true })).toBeVisible();
  const field = page.getByLabel("访问密码");
  const button = page.getByRole("button", { name: /登\s*录$/ });
  if (checkWrongPassword) {
    await field.fill("wrong-browser-test-password");
    await button.click();
    await expect(page.getByRole("alert")).toContainText("访问密码不正确");
  }
  await field.fill(runtime.password);
  await button.click();
  await expect(page.getByRole("heading", { name: "我的小说" })).toBeVisible();
}

test("登录后创建并编辑书稿，删卷保留章节，按需打开编辑器并导出", async ({
  page,
  context,
  runtime,
}, testInfo) => {
  const title = "浏览器回归测试书";
  const body = "合成正文。角色在码头找到了一封蓝色信件。";
  const markdownAsset = /\/assets\/(?:index\.common-|markdown-highlighting-)/;
  const assets = [];
  page.on("request", (request) => {
    if (markdownAsset.test(request.url())) assets.push(request.url());
  });

  await test.step("验证会话并从书架创建空书", async () => {
    await login(page, runtime, { checkWrongPassword: true });
    expect(await context.cookies(runtime.origin)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: "novel_forge_session", httpOnly: true }),
      ])
    );
    await page.getByRole("button", { name: /新建空书/ }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("书名").fill(title);
    await dialog.getByRole("button", { name: /创\s*建/ }).click();
    await page.getByRole("button", { name: new RegExp(title) }).click();
    await expect(page).toHaveURL(/\/books\/[^/?]+$/);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  });
  const bookPath = new URL(page.url()).pathname;
  const tree = page.locator(".nf-catalog");
  const volume = tree.locator(".ant-tree-treenode").filter({
    has: page.getByText("第 1 卷", { exact: true }),
  });
  const editor = page.locator(".nf-editor");
  const textArea = editor.getByPlaceholder(/点击“生成正文”/);
  const menu = (name) => page.getByRole("menuitem", { name, exact: true });

  await test.step("在卷内创建章节，保存正文后即时更新状态和字数", async () => {
    await page.getByRole("button", { name: "新增卷" }).click();
    await volume.getByRole("img", { name: "more" }).click();
    await menu("新增章节").click();
    await tree.getByText("第 1 章", { exact: true }).click();
    await expect(editor.getByText("空白", { exact: true })).toBeVisible();
    await textArea.fill(body);
    await editor.getByRole("button", { name: /保\s*存/ }).click();
    await expect(editor.getByText("已完成", { exact: true })).toBeVisible();
    await expect(tree.getByText(`${body.length}字`, { exact: true })).toBeVisible();
  });

  await test.step("删除卷后重载工作区，章节和正文仍在未归卷", async () => {
    await volume.getByRole("img", { name: "more" }).click();
    await menu("删除").click();
    const confirm = page.getByRole("dialog");
    await expect(confirm).toContainText("卷内章节及正文会保留");
    const [deleted] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "DELETE"),
      confirm.getByRole("button", { name: "删除卷" }).click(),
    ]);
    expect(deleted.ok()).toBe(true);
    await page.reload();
    await expect(volume).toHaveCount(0);
    await expect(tree.getByText("未归卷", { exact: true })).toBeVisible();
    await tree.getByText(/第 1 章/).click();
    await expect(textArea).toHaveValue(body);
    await expect(editor.getByText("已完成", { exact: true })).toBeVisible();
  });

  await test.step("人物卡按需加载 Markdown，剧情面板可以打开", async () => {
    expect(assets).toHaveLength(0);
    await page.getByRole("menuitem", { name: /人物/ }).click();
    await page.getByRole("button", { name: /新增/ }).click();
    const markdown = page.locator(".w-md-editor textarea");
    await markdown.fill("# 合成人物\n\n```python\nprint('test')\n```");
    expect(assets.length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "保存人物卡" }).click();
    await expect(page.getByText("已保存", { exact: true })).toBeVisible();
    await expect(
      page.locator(".wmde-markdown").getByRole("heading", { name: "合成人物" })
    ).toBeVisible();
    await page.getByRole("menuitem", { name: /剧情/ }).click();
    await expect(page.getByText("剧情进展", { exact: true })).toBeVisible();
    await expect(page.getByText("尚未创建剧情线程", { exact: true })).toBeVisible();
  });

  await test.step("下载两种导出格式并验证登录退出", async () => {
    for (const format of ["txt", "epub"]) {
      await page.getByRole("button", { name: /导\s*出/ }).hover();
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        menu(`导出 ${format.toUpperCase()}`).click(),
      ]);
      expect(download.suggestedFilename()).toBe(`${title}.${format}`);
      const file = testInfo.outputPath(`export.${format}`);
      await download.saveAs(file);
      const contents = await readFile(file);
      if (format === "txt") {
        expect(contents.toString("utf8")).toContain(body);
      } else {
        expect(contents.subarray(0, 4).toString("hex")).toBe("504b0304");
        expect(contents.toString("ascii")).toContain("application/epub+zip");
      }
      await page.getByRole("heading", { name: title }).hover();
    }
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(page.getByText("登录小说工作台", { exact: true })).toBeVisible();
    const response = await context.request.get(`${runtime.origin}/api${bookPath}`);
    expect(response.status()).toBe(401);
  });
});

test("两个窗口编辑同一章时保留冲突草稿，确认恢复后才能继续保存", async ({
  page,
  context,
  runtime,
}) => {
  await login(page, runtime);
  const bookResponse = await context.request.post(`${runtime.origin}/api/books`, {
    headers: { Origin: runtime.origin },
    data: { title: "多窗口保存测试" },
  });
  await expect(bookResponse).toBeOK();
  const book = await bookResponse.json();
  const chapterResponse = await context.request.post(`${runtime.origin}/api/chapters`, {
    headers: { Origin: runtime.origin },
    data: { book_id: book.id, title: "冲突章节", outline: "合成章纲" },
  });
  await expect(chapterResponse).toBeOK();
  const chapter = await chapterResponse.json();
  const contentUrl = `${runtime.origin}/api/chapters/${chapter.id}/content`;
  const otherPage = await context.newPage();
  const errors = [];
  otherPage.on("pageerror", (error) => errors.push(error.message));
  await otherPage.route("**/*", (route) =>
    new URL(route.request().url()).origin === runtime.origin
      ? route.continue()
      : route.abort("blockedbyclient")
  );
  const editor = (current) => current.locator(".nf-editor");
  const body = (current) => editor(current).getByPlaceholder(/点击“生成正文”/);
  const save = (current) => editor(current).getByRole("button", { name: /保\s*存$/ });
  const contentSave = (current) =>
    current.waitForResponse(
      (response) => response.url() === contentUrl && response.request().method() === "PUT"
    );
  try {
    for (const current of [page, otherPage]) {
      await current.goto(`${runtime.origin}/books/${book.id}`);
      await current.locator(".nf-catalog").getByText("冲突章节", { exact: true }).click();
      await expect(body(current)).toHaveValue("");
    }
    await body(page).fill("第一个窗口已保存的正文");
    const [saved] = await Promise.all([contentSave(page), save(page).click()]);
    expect(saved.status()).toBe(200);
    await body(otherPage).fill("第二个窗口尚未合并的草稿");
    const [conflict] = await Promise.all([contentSave(otherPage), save(otherPage).click()]);
    expect(conflict.status()).toBe(409);
    await expect(
      otherPage.getByText("服务器正文已有新版本，本地草稿已保留", { exact: true })
    ).toBeVisible();
    await expect(body(otherPage)).toHaveValue("第二个窗口尚未合并的草稿");
    await expect(save(otherPage)).toBeDisabled();

    await otherPage.getByRole("button", { name: "重新加载正文", exact: true }).click();
    await otherPage.getByRole("dialog").getByRole("button", { name: "保留本地草稿" }).click();
    await expect(otherPage.getByRole("dialog")).toBeHidden();
    await expect(body(otherPage)).toHaveValue("第二个窗口尚未合并的草稿");
    expect((await (await context.request.get(contentUrl)).json()).body).toBe(
      "第一个窗口已保存的正文"
    );

    await otherPage.getByRole("button", { name: "重新加载正文", exact: true }).click();
    await otherPage.getByRole("dialog").getByRole("button", { name: "替换并重新加载" }).click();
    await expect(otherPage.getByRole("dialog")).toBeHidden();
    await expect(body(otherPage)).toHaveValue("第一个窗口已保存的正文");
    await body(otherPage).fill("两个窗口手工合并后的正文");
    const [merged] = await Promise.all([contentSave(otherPage), save(otherPage).click()]);
    expect(merged.status()).toBe(200);
    expect((await merged.json()).body).toBe("两个窗口手工合并后的正文");
    expect(errors).toEqual([]);
  } finally {
    await otherPage.close();
  }
});

test("轨迹向前翻页保持阅读位置，接收新事件并在任务结束后补拉", async ({ page, runtime }) => {
  await login(page, runtime);
  const database = new DatabaseSync(runtime.database);
  try {
    database.exec("PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000");
    const book = randomUUID();
    const task = randomUUID();
    const record = database.prepare(
      "INSERT INTO agent_trace_entries (id, task_id, sequence, step_index, kind, payload) VALUES (?, ?, ?, ?, 'thought', ?)"
    );
    const append = (index) =>
      record.run(
        randomUUID(),
        task,
        index,
        index,
        `合成轨迹 ${String(index).padStart(3, "0")}。检查轨迹分页与阅读位置。`
      );
    database.exec("BEGIN IMMEDIATE");
    database.prepare("INSERT INTO books (id, title) VALUES (?, ?)").run(book, "合成轨迹测试书");
    database
      .prepare(
        "INSERT INTO generation_tasks (id, book_id, type, status, progress, trace_sequence) VALUES (?, ?, 'agent', 'running', 35, 55)"
      )
      .run(task, book);
    for (let index = 1; index <= 55; index += 1) append(index);
    database.exec("COMMIT");

    await page.goto(`${runtime.origin}/agent?bookId=${book}&taskId=${task}`);
    const region = page.getByRole("region", { name: "执行轨迹" });
    const event = (index) => region.getByText(new RegExp(`合成轨迹 ${index}`));
    await expect(event("055")).toBeVisible();
    await expect(event("001")).toHaveCount(0);
    await expect(region.locator(".nf-bubble")).toHaveCount(50);

    await region.evaluate((element) => (element.scrollTop = 0));
    const anchor = region.locator(".nf-bubble").filter({ hasText: /合成轨迹 006/ });
    // Measure the scroll anchor after the entry's rise animation settles.
    await anchor.evaluate(async (element) => {
      await Promise.allSettled(element.getAnimations().map((animation) => animation.finished));
    });
    const previousTop = await anchor.evaluate((element) => element.getBoundingClientRect().top);
    await page.getByRole("button", { name: "加载更早轨迹" }).click();
    await expect(event("001")).toHaveCount(1);
    await expect(region.locator(".nf-bubble")).toHaveCount(55);
    const currentTop = await anchor.evaluate((element) => element.getBoundingClientRect().top);
    expect(Math.abs(currentTop - previousTop)).toBeLessThanOrEqual(2);

    const before = await region.evaluate((element) => element.scrollTop);
    database.exec("BEGIN IMMEDIATE");
    append(56);
    database.prepare("UPDATE generation_tasks SET trace_sequence = 56 WHERE id = ?").run(task);
    database.exec("COMMIT");
    const jump = page.getByRole("button", { name: "有新轨迹，跳到最新" });
    await expect(jump).toBeVisible();
    await expect(event("056")).toHaveCount(1);
    const after = await region.evaluate((element) => element.scrollTop);
    expect(Math.abs(after - before)).toBeLessThanOrEqual(2);
    await jump.click();
    await expect
      .poll(() =>
        region.evaluate(
          (element) => element.scrollHeight - element.clientHeight - element.scrollTop
        )
      )
      .toBeLessThanOrEqual(2);

    database.exec("BEGIN IMMEDIATE");
    append(57);
    database
      .prepare(
        "UPDATE generation_tasks SET status = 'done', progress = 100, trace_sequence = 57 WHERE id = ?"
      )
      .run(task);
    database.exec("COMMIT");
    await expect(region.getByText(/创作完成/)).toBeVisible();
    await expect(event("057")).toHaveCount(1);
  } finally {
    database.close();
  }
});
