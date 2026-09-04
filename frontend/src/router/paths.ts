// 路由路径统一在这里维护,避免各页面各自拼接字符串导致跳转路径与路由定义不一致。

function bookPath(
  bookId: number | string,
  query: { view?: string; fromTask?: number | string | null } = {}
): string {
  const params = new URLSearchParams();
  if (query.view) params.set("view", query.view);
  if (query.fromTask != null) params.set("fromTask", String(query.fromTask));
  const qs = params.toString();
  return qs ? `/books/${bookId}?${qs}` : `/books/${bookId}`;
}

export const paths = {
  home: "/",
  book: (bookId: number | string, fromTask?: number | string | null) =>
    bookPath(bookId, { fromTask }),
  bookTasks: (bookId: number | string, fromTask?: number | string | null) =>
    bookPath(bookId, { view: "tasks", fromTask }),
  agentChat: "/agent",
  agentChatFor: (bookId: number | string, taskId: number | string) =>
    `/agent?bookId=${bookId}&taskId=${taskId}`,
} as const;

// 供 <Route path=... /> 使用的路由匹配模式
export const routePatterns = {
  home: "/",
  book: "/books/:bookId",
  agentChat: "/agent",
} as const;
