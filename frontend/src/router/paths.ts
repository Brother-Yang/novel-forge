// 路由路径统一在这里维护,避免各页面各自拼接字符串导致跳转路径与路由定义不一致。

export const paths = {
  home: "/",
  book: (bookId: number | string) => `/books/${bookId}`,
  bookTasks: (bookId: number | string) => `/books/${bookId}?view=tasks`,
} as const;

// 供 <Route path=... /> 使用的路由匹配模式
export const routePatterns = {
  home: "/",
  book: "/books/:bookId",
} as const;
