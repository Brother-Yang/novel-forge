import { useState } from "react";
import { Layout, Menu, Button, Spin, Dropdown, Alert, App } from "antd";
import {
  ArrowLeftOutlined,
  BookOutlined,
  DownloadOutlined,
  GlobalOutlined,
  RobotOutlined,
  NodeIndexOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import CatalogTree from "../components/CatalogTree";
import ChapterEditor from "../components/ChapterEditor";
import AsyncContent from "../components/AsyncContent";
import AuthSignOut from "../components/AuthSignOut";
import { useBook } from "../hooks/useBook";
import { paths } from "../router/paths";
import { downloadFile } from "../utils/download";

const { Sider, Content, Header } = Layout;
const loadCharacters = () => import("../components/CharacterPanel");
const loadWorldbuilding = () => import("../components/WorldbuildingPanel");
const loadTasks = () => import("../components/TaskPanel");
const loadPlotThreads = () => import("../components/PlotThreadPanel");

const views = ["chapters", "characters", "world", "threads", "tasks"] as const;
type View = (typeof views)[number];

function isView(value: string | null): value is View {
  return views.some((view) => view === value);
}

export default function BookWorkspace() {
  const { bookId } = useParams();
  if (!bookId) return <Alert type="error" message="缺少书籍标识" />;
  return <Workspace key={bookId} bookId={bookId} />;
}

interface WorkspaceProps {
  bookId: string;
}

function Workspace({ bookId }: WorkspaceProps) {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const fromTask = searchParams.get("fromTask");
  const requestedView = searchParams.get("view");
  const view = isView(requestedView) ? requestedView : "chapters";
  const [selectedChapter, setSelectedChapter] = useState<string | null>(null);
  const [taskActive, setTaskActive] = useState(false);
  const [exporting, setExporting] = useState(false);
  const { book, error, loading, retry } = useBook(bookId, view === "tasks" && taskActive);

  const exportBook = async (format: "txt" | "epub") => {
    if (!book || exporting) return;
    setExporting(true);
    try {
      await downloadFile(api.exportUrl(bookId, format), `${book.title}.${format}`);
    } catch (err) {
      void message.error(err instanceof Error ? err.message : "导出失败，请重试");
    } finally {
      setExporting(false);
    }
  };

  const failure = error ? (
    <Alert
      type="error"
      showIcon
      message={error}
      action={
        <Button aria-label="重新加载" loading={loading} onClick={retry}>
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
            onClick: ({ key }) => {
              if (key === "txt" || key === "epub") void exportBook(key);
            },
          }}
        >
          <Button className="nf-ghost" icon={<DownloadOutlined />} loading={exporting}>
            导出
          </Button>
        </Dropdown>
        <AuthSignOut />
      </Header>
      {failure}
      <Layout>
        <Sider className="nf-rail" width={80} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[view]}
            onClick={({ key }) => {
              if (!isView(key) || key === view) return;
              const next = new URLSearchParams(searchParams);
              if (key === "chapters") next.delete("view");
              else next.set("view", key);
              setSearchParams(next, { replace: true });
            }}
            inlineCollapsed
            items={[
              { key: "chapters", icon: <BookOutlined />, label: "章节" },
              { key: "characters", icon: <TeamOutlined />, label: "人物" },
              { key: "world", icon: <GlobalOutlined />, label: "设定" },
              { key: "threads", icon: <NodeIndexOutlined />, label: "剧情" },
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
              componentProps={{ bookId, onGenerationActivityChange: setTaskActive }}
              label="生成面板"
            />
          )}
          {view === "threads" && (
            <AsyncContent
              key="threads"
              load={loadPlotThreads}
              componentProps={{ bookId }}
              label="剧情面板"
            />
          )}
        </Content>
      </Layout>
    </Layout>
  );
}
