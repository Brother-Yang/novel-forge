import { useEffect, useState } from "react";
import { Layout, Menu, Button, Typography, Spin, Dropdown } from "antd";
import {
  ArrowLeftOutlined,
  BookOutlined,
  DownloadOutlined,
  GlobalOutlined,
  RobotOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Book } from "../api/types";
import CatalogTree from "../components/CatalogTree";
import ChapterEditor from "../components/ChapterEditor";
import CharacterPanel from "../components/CharacterPanel";
import WorldbuildingPanel from "../components/WorldbuildingPanel";
import TaskPanel from "../components/TaskPanel";
import { paths } from "../router/paths";
import { downloadFile } from "../utils/download";

const { Sider, Content, Header } = Layout;

type View = "chapters" | "characters" | "world" | "tasks";

export default function BookWorkspace() {
  const { bookId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const fromTask = searchParams.get("fromTask");
  const [book, setBook] = useState<Book | null>(null);
  const [view, setView] = useState<View>(
    searchParams.get("view") === "tasks" ? "tasks" : "chapters"
  );
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);

  useEffect(() => {
    if (!bookId) return;
    api
      .getBook(bookId)
      .then(setBook)
      .catch(() => {});
  }, [bookId]);

  useEffect(() => {
    if (!bookId || view !== "tasks") return;
    const timer = setInterval(() => {
      api
        .getBook(bookId)
        .then(setBook)
        .catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [bookId, view]);

  const exportBook = (format: "txt" | "epub") => {
    if (!bookId) return;
    downloadFile(api.exportUrl(bookId, format));
  };

  if (!book || !bookId) return <Spin style={{ margin: 80 }} />;

  return (
    <Layout style={{ height: "100vh" }}>
      <Header
        style={{
          background: "#fff",
          display: "flex",
          alignItems: "center",
          gap: 12,
          borderBottom: "1px solid #f0f0f0",
          paddingInline: 16,
        }}
      >
        <Button
          icon={<ArrowLeftOutlined />}
          type="text"
          onClick={() =>
            navigate(
              fromTask && bookId ? paths.agentChatFor(bookId, fromTask) : paths.home
            )
          }
        />
        <Typography.Title level={4} style={{ margin: 0 }}>
          {book.title}
        </Typography.Title>
        <Typography.Text type="secondary">
          {book.genre} · 目标 {book.target_words.toLocaleString()} 字
        </Typography.Text>
        <div style={{ flex: 1 }} />
        {fromTask && bookId && (
          <Button
            icon={<RobotOutlined />}
            onClick={() => navigate(paths.agentChatFor(bookId, fromTask))}
          >
            返回 AI 创作
          </Button>
        )}
        <Dropdown
          menu={{
            items: [
              { key: "txt", label: "导出 TXT" },
              { key: "epub", label: "导出 EPUB" },
            ],
            onClick: ({ key }) => exportBook(key as "txt" | "epub"),
          }}
        >
          <Button icon={<DownloadOutlined />}>导出</Button>
        </Dropdown>
      </Header>
      <Layout>
        <Sider width={80} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
          <Menu
            mode="inline"
            selectedKeys={[view]}
            onClick={(e) => setView(e.key as View)}
            inlineCollapsed
            items={[
              { key: "chapters", icon: <BookOutlined />, label: "章节" },
              { key: "characters", icon: <TeamOutlined />, label: "人物" },
              { key: "world", icon: <GlobalOutlined />, label: "设定" },
              { key: "tasks", icon: <ThunderboltOutlined />, label: "生成" },
            ]}
          />
        </Sider>

        {view === "chapters" && (
          <Sider
            width={320}
            theme="light"
            style={{ borderRight: "1px solid #f0f0f0", overflow: "auto" }}
          >
            <CatalogTree
              bookId={bookId}
              selectedChapter={selectedChapter}
              onSelectChapter={setSelectedChapter}
            />
          </Sider>
        )}

        <Content style={{ overflow: "auto", background: "#fafafa" }}>
          {view === "chapters" && (
            <ChapterEditor
              key={selectedChapter ?? "none"}
              bookId={bookId}
              chapterId={selectedChapter}
            />
          )}
          {view === "characters" && <CharacterPanel bookId={bookId} />}
          {view === "world" && <WorldbuildingPanel bookId={bookId} />}
          {view === "tasks" && <TaskPanel bookId={bookId} />}
        </Content>
      </Layout>
    </Layout>
  );
}
