import { useEffect, useState } from "react";
import { App, Button, Form, Input, InputNumber, Modal, Popconfirm, Space, Typography } from "antd";
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
import { paths } from "../router/paths";

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "请求失败";
}

export default function BookList() {
  const [books, setBooks] = useState<Book[]>([]);
  const [emptyOpen, setEmptyOpen] = useState(false);
  const [novelOpen, setNovelOpen] = useState(false);
  const [emptyForm] = Form.useForm();
  const [novelForm] = Form.useForm();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const load = () =>
    api
      .listBooks()
      .then(setBooks)
      .catch((err: unknown) => message.error(errMessage(err)));
  useEffect(() => {
    load();
    // 仅在进入列表页时拉一次;message 来自 antd context,不必加入依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onCreateEmpty = async () => {
    try {
      const values = await emptyForm.validateFields();
      await api.createBook(values);
      setEmptyOpen(false);
      emptyForm.resetFields();
      message.success("已创建");
      load();
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(errMessage(err));
    }
  };

  const onCreateNovel = async () => {
    try {
      const values = await novelForm.validateFields();
      const result = await api.startNovelGeneration({
        brief: values.brief,
        target_words: values.target_words,
      });
      setNovelOpen(false);
      novelForm.resetFields();
      message.success("已开始自动生成");
      navigate(paths.bookTasks(result.book.id));
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      message.error(errMessage(err));
    }
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
        </Space>
      </header>

      <section className="nf-library__hero">
        <h1>我的小说</h1>
        <p className="nf-library__lede">
          长篇不怕长，怕写到后面不认人。人设、世界、伏笔，一章章接着往下认。
        </p>
      </section>

      {books.length === 0 ? (
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
                  onConfirm={async (e) => {
                    e?.stopPropagation();
                    try {
                      await api.deleteBook(b.id);
                      load();
                    } catch (err) {
                      message.error(errMessage(err));
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
