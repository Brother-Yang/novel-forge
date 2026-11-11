import { useEffect, useState } from "react";
import { Layout, Menu, Button, Spin, Dropdown } from "antd";
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
  const [selectedChapter, setSelectedChapter] = useState<string | null>(null);

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

  if (!book || !bookId) {
    return (
      <div className="nf-boot">
        <span className="nf-brand__seal">墨</span>
        <Spin />
      </div>
    );
  }

  return (
    <Layout className="nf-studio">
      <Header className="nf-studio__header">
        <Button
          className="nf-ghost"
          icon={<ArrowLeftOutlined />}
          type="text"
          onClick={() =>
            navigate(fromTask && bookId ? paths.agentChatFor(bookId, fromTask) : paths.home)
          }
        />
        <h1 className="nf-studio__title">{book.title}</h1>
        <span className="nf-studio__meta">
          {book.genre} · 目标 {book.target_words.toLocaleString()} 字
        </span>
        <div style={{ flex: 1 }} />
        {fromTask && bookId && (
          <Button
            className="nf-ghost"
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
          <Button className="nf-ghost" icon={<DownloadOutlined />}>
            导出
          </Button>
        </Dropdown>
      </Header>
      <Layout>
        <Sider className="nf-rail" width={80} theme="light">
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
          <Sider className="nf-manila" width={300} theme="light" style={{ overflow: "auto" }}>
            <CatalogTree
              bookId={bookId}
              selectedChapter={selectedChapter}
              onSelectChapter={setSelectedChapter}
            />
          </Sider>
        )}

        <Content className="nf-manuscript">
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
