import { useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Space,
  Typography,
} from "antd";
import { PlusOutlined, DeleteOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Book } from "../api/types";
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
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 32 }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Typography.Title level={2} style={{ margin: 0 }}>
          我的小说
        </Typography.Title>
        <Space>
          <Button onClick={() => setEmptyOpen(true)} icon={<PlusOutlined />}>
            新建空书
          </Button>
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setNovelOpen(true)}>
            一句话出书
          </Button>
        </Space>
      </Row>

      <Row gutter={[16, 16]}>
        {books.map((b) => (
          <Col key={b.id} xs={24} sm={12} md={8}>
            <Card
              hoverable
              onClick={() => navigate(paths.book(b.id))}
              title={b.title}
              extra={
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
                  <DeleteOutlined onClick={(e) => e.stopPropagation()} style={{ color: "#999" }} />
                </Popconfirm>
              }
            >
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                {b.intro || b.brief || "暂无简介"}
              </Typography.Paragraph>
              <div style={{ color: "#888", fontSize: 12 }}>
                {b.genre || "未分类"} · 目标 {b.target_words.toLocaleString()} 字
              </div>
            </Card>
          </Col>
        ))}
        {books.length === 0 && (
          <Col span={24}>
            <Typography.Text type="secondary">
              还没有书籍,点击右上角「一句话出书」或「新建空书」。
            </Typography.Text>
          </Col>
        )}
      </Row>

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
