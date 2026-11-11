import type { ThemeConfig } from "antd";

/** 墨夜书坊:朱砂印、黄铜边、稿纸底。页面底色由 CSS 负责,Ant Design 组件走稿纸浅色。 */
export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: "#c23b22",
    colorInfo: "#8c5a3c",
    colorSuccess: "#5a7d4e",
    colorWarning: "#b8893e",
    colorError: "#c23b22",
    colorLink: "#c23b22",
    borderRadius: 4,
    fontFamily: '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
    colorText: "#2c241c",
    colorTextSecondary: "#6b5e52",
    colorTextTertiary: "#8a7d6f",
    colorBorder: "#d9cbb6",
    colorBorderSecondary: "#e8dcc8",
    colorBgContainer: "#faf6ee",
    colorBgElevated: "#faf6ee",
    colorBgLayout: "transparent",
    colorBgSpotlight: "#2a2118",
    controlOutline: "rgba(194, 59, 34, 0.18)",
    boxShadow: "0 18px 40px rgba(22, 17, 12, 0.22)",
    boxShadowSecondary: "0 8px 20px rgba(22, 17, 12, 0.12)",
  },
  components: {
    Layout: {
      headerBg: "transparent",
      siderBg: "transparent",
      bodyBg: "transparent",
      headerPadding: "0 22px",
      headerHeight: 64,
    },
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      dangerShadow: "none",
      fontWeight: 500,
    },
    Card: {
      colorBgContainer: "#faf6ee",
    },
    Modal: {
      contentBg: "#faf6ee",
      headerBg: "#faf6ee",
      titleFontSize: 18,
    },
    Input: {
      activeBorderColor: "#c23b22",
      hoverBorderColor: "#c4a574",
    },
    Tree: {
      nodeSelectedBg: "rgba(194, 59, 34, 0.12)",
      nodeHoverBg: "rgba(196, 165, 116, 0.18)",
    },
    Tag: {
      defaultBg: "#efe4cc",
      defaultColor: "#6b5e52",
    },
    Progress: {
      defaultColor: "#c23b22",
    },
    Collapse: {
      headerBg: "#f3ead8",
      contentBg: "#faf6ee",
    },
  },
};
