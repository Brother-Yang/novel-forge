import { useEffect, useState } from "react";
import { Layout, Menu, Button, Spin, Dropdown, Alert } from "antd";
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
import AsyncContent from "../components/AsyncContent";
import { paths } from "../router/paths";
import { downloadFile } from "../utils/download";

const { Sider, Content, Header } = Layout;
const loadCharacters = () => import("../components/CharacterPanel");
const loadWorldbuilding = () => import("../components/WorldbuildingPanel");
const loadTasks = () => import("../components/TaskPanel");

type View = "chapters" | "characters" | "world" | "tasks";

export default function BookWorkspace() {
  const { bookId } = useParams();
  if (!bookId) return <Alert type="error" message="缺少书籍标识" />;
  return <Workspace key={bookId} bookId={bookId} />;
}

interface WorkspaceProps {
  bookId: string;
}

function Workspace({ bookId }: WorkspaceProps) {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const fromTask = searchParams.get("fromTask");
  const [book, setBook] = useState<Book | null>(null);
  const [view, setView] = useState<View>(
    searchParams.get("view") === "tasks" ? "tasks" : "chapters"
  );
  const [selectedChapter, setSelectedChapter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      setLoading(true);
      try {
        const data = await api.getBook(bookId);
        if (!active) return;
        setBook(data);
        setError(null);
      } catch (err: unknown) {
        if (active) setError(err instanceof Error ? err.message : "书籍加载失败");
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = view === "tasks" ? setInterval(() => void load(), 4000) : undefined;
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [bookId, view, retry]);

  const exportBook = (format: "txt" | "epub") => {
    if (!bookId) return;
    downloadFile(api.exportUrl(bookId, format));
  };

  const failure = error ? (
    <Alert
      type="error"
      showIcon
      message={error}
      action={
        <Button loading={loading} onClick={() => setRetry((value) => value + 1)}>
          重新加载
        </Button>
      }
    />
  ) : null;

  if (!book) {
    return (
      <div className="nf-boot">
        <span className="nf-brand__seal">墨</span>
        {failure ?? <Spin aria-label="书籍加载中" />}
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
      {failure}
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
          {view === "characters" && (
            <AsyncContent
              key="characters"
              load={loadCharacters}
              componentProps={{ bookId }}
              label="人物面板"
            />
          )}
          {view === "world" && (
            <AsyncContent
              key="world"
              load={loadWorldbuilding}
              componentProps={{ bookId }}
              label="设定面板"
            />
          )}
          {view === "tasks" && (
            <AsyncContent
              key="tasks"
              load={loadTasks}
              componentProps={{ bookId }}
              label="生成面板"
            />
          )}
        </Content>
      </Layout>
    </Layout>
  );
}
