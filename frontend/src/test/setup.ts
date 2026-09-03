import "@testing-library/jest-dom/vitest";

// antd 的响应式栅格(Row/Col)依赖 matchMedia,jsdom 未实现,这里补一个最小 mock
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
