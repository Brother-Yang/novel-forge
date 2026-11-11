import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});

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

// jsdom 没有伪元素布局。AntD 仅用该参数测量滚动条,退回元素样式即可;
// 保留真实 getComputedStyle,不屏蔽其它控制台错误或浏览器行为。
const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element) => getComputedStyle(element);
