import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Typography,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Book } from "../api/types";
import BrandMark from "../components/BrandMark";
import AuthSignOut from "../components/AuthSignOut";
import { TASK_START_ERROR } from "../constants/taskStatus";
import { usePendingAction } from "../hooks/usePendingAction";
import { paths } from "../router/paths";
import { useAppStore } from "../store";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

export default function BookList() {
  const [books, setBooks] = useState<Book[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const listRequest = useRef<AbortController | null>(null);
  const [emptyOpen, setEmptyOpen] = useState(false);
  const [novelOpen, setNovelOpen] = useState(false);
  const [emptyForm] = Form.useForm();
  const [novelForm] = Form.useForm();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { pending: submitting, run } = usePendingAction();

  const load = useCallback(() => {
    listRequest.current?.abort();
    const request = new AbortController();
    listRequest.current = request;
    void api.listBooks(request.signal).then(
      (data) => {
        if (request.signal.aborted) return;
        setBooks(data);
        setLoadError(null);
        setLoading(false);
      },
      (err: unknown) => {
        if (request.signal.aborted) return;
        setLoadError(errMessage(err));
        setLoading(false);
      }
    );
  }, []);
  useEffect(() => {
    load();
    return () => {
      listRequest.current?.abort();
      listRequest.current = null;
    };
  }, [load]);

  const reload = () => {
    setLoading(true);
    load();
  };

  const onCreateEmpty = () =>
    run(async (isCurrent) => {
      try {
        const values = await emptyForm.validateFields();
        if (!isCurrent()) return;
        await api.createBook(values);
        if (!isCurrent()) return;
        setEmptyOpen(false);
        emptyForm.resetFields();
        message.success("已创建");
        reload();
      } catch (err) {
        if (err && typeof err === "object" && "errorFields" in err) return;
        if (isCurrent()) message.error(errMessage(err));
      }
    });

  const onCreateNovel = () =>
    run(async (isCurrent) => {
      try {
        const values = await novelForm.validateFields();
        if (!isCurrent()) return;
        const result = await api.startNovelGeneration({
          brief: values.brief,
          target_words: values.target_words,
        });
        if (!isCurrent()) return;
        setNovelOpen(false);
        novelForm.resetFields();
        if (result.task.status === "error") message.error(result.task.error || TASK_START_ERROR);
        else message.success("已开始自动生成");
        navigate(paths.bookTasks(result.book.id));
      } catch (err) {
        if (err && typeof err === "object" && "errorFields" in err) return;
        if (isCurrent()) message.error(errMessage(err));
      }
    });

  const modalBusy = {
    confirmLoading: submitting,
    cancelButtonProps: { disabled: submitting },
    closable: !submitting,
    keyboard: !submitting,
    maskClosable: !submitting,
  };

  return (
    <div className="nf-page nf-library">
      <header className="nf-library__top">
        <BrandMark />
        <Space wrap>
          <Button className="nf-ghost" onClick={() => setEmptyOpen(true)} icon={<PlusOutlined />}>
            新建空书
          </Button>
          <Button
            className="nf-ghost"
            icon={<ThunderboltOutlined />}
            onClick={() => setNovelOpen(true)}
          >
            一句话出书
          </Button>
          <Button
            className="nf-ghost"
            type="primary"
            icon={<RobotOutlined />}
            onClick={() => navigate(paths.agentChat)}
          >
            AI 自主创作
          </Button>
          <AuthSignOut />
        </Space>
      </header>

      <section className="nf-library__hero">
        <h1>我的小说</h1>
        <p className="nf-library__lede">
          长篇不怕长，怕写到后面不认人。人设、世界、伏笔，一章章接着往下认。
        </p>
      </section>

      {loadError && (
        <Alert
          type="error"
          showIcon
          message={`书架加载失败：${loadError}`}
          action={
            <Button aria-label="重新加载书架" loading={loading} onClick={reload}>
              重新加载书架
            </Button>
          }
        />
      )}
      {books === null ? (
        !loadError && (
          <div className="nf-empty" role="status">
            <Spin size="small" />
            <Typography.Text type="secondary">正在加载书架…</Typography.Text>
          </div>
        )
      ) : books.length === 0 ? (
        <div className="nf-empty">
          <span className="nf-empty__glyph">空</span>
          <Typography.Text type="secondary">
            还没有书籍,点击右上角「一句话出书」或「新建空书」。
          </Typography.Text>
        </div>
      ) : (
        <div className="nf-book-grid">
          {books.map((b, i) => (
            <article
              key={b.id}
              className="nf-book-card"
              style={{ animationDelay: `${i * 60}ms` }}
              role="button"
              tabIndex={0}
              onClick={() => navigate(paths.book(b.id))}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  navigate(paths.book(b.id));
                }
              }}
            >
              <div className="nf-book-card__head">
                <h3>{b.title}</h3>
                <Popconfirm
                  title="删除该书籍?"
                  description="该书在本窗口的未保存正文也会清除。"
                  onConfirm={async (e) => {
                    e?.stopPropagation();
                    try {
                      await api.deleteBook(b.id);
                      useAppStore.getState().clearBookDrafts(b.id);
                      if (listRequest.current) reload();
                    } catch (err) {
                      if (listRequest.current) message.error(errMessage(err));
                    }
                  }}
                >
                  <DeleteOutlined
                    className="nf-book-card__delete"
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>
              </div>
              <p className="nf-book-card__intro">{b.intro || "暂无简介"}</p>
              <div className="nf-book-card__meta">
                <span className="nf-seal">{b.genre || "未分类"}</span>
                <span>目标 {b.target_words.toLocaleString()} 字</span>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        {...modalBusy}
        title="一句话出书"
        open={novelOpen}
        onOk={onCreateNovel}
        onCancel={() => setNovelOpen(false)}
        okText="开始生成"
        cancelText="取消"
      >
        <Form form={novelForm} layout="vertical" initialValues={{ target_words: 100000 }}>
          <Form.Item
            name="brief"
            label="故事想法"
            rules={[{ required: true, message: "请用一段话描述你想写的故事" }]}
          >
            <Input.TextArea
              rows={5}
              placeholder="例如:少年林逸在灵气复苏之夜觉醒上古血脉,从宗门杂役一步步踏上与妖族争锋的路。"
            />
          </Form.Item>
          <Form.Item name="target_words" label="目标字数">
            <InputNumber min={10000} step={10000} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        {...modalBusy}
        title="新建空书"
        open={emptyOpen}
        onOk={onCreateEmpty}
        onCancel={() => setEmptyOpen(false)}
        okText="创建"
        cancelText="取消"
      >
        <Form form={emptyForm} layout="vertical" initialValues={{ target_words: 100000 }}>
          <Form.Item name="title" label="书名" rules={[{ required: true }]}>
            <Input placeholder="例如:剑起苍穹" />
          </Form.Item>
          <Form.Item name="genre" label="题材">
            <Input placeholder="玄幻 / 都市 / 科幻 ..." />
          </Form.Item>
          <Form.Item name="tone" label="基调">
            <Input placeholder="热血 / 轻松 / 悬疑 ..." />
          </Form.Item>
          <Form.Item name="intro" label="简介">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="target_words" label="目标字数">
            <InputNumber min={10000} step={10000} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
