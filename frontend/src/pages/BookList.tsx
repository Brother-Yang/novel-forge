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
  Typography,
} from "antd";
import { PlusOutlined, DeleteOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Book } from "../api/types";

export default function BookList() {
  const [books, setBooks] = useState<Book[]>([]);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const load = () => api.listBooks().then(setBooks);
  useEffect(() => {
    load();
  }, []);

  const onCreate = async () => {
    const values = await form.validateFields();
    await api.createBook(values);
    setOpen(false);
    form.resetFields();
    message.success("已创建");
    load();
  };

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 32 }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Typography.Title level={2} style={{ margin: 0 }}>
          我的小说
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建书籍
        </Button>
      </Row>

      <Row gutter={[16, 16]}>
        {books.map((b) => (
          <Col key={b.id} xs={24} sm={12} md={8}>
            <Card
              hoverable
              onClick={() => navigate(`/books/${b.id}`)}
              title={b.title}
              extra={
                <Popconfirm
                  title="删除该书籍?"
                  onConfirm={async () => {
                    await api.deleteBook(b.id);
                    load();
                  }}
                >
                  <DeleteOutlined
                    onClick={(e) => e.stopPropagation()}
                    style={{ color: "#999" }}
                  />
                </Popconfirm>
              }
            >
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                {b.intro || "暂无简介"}
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
              还没有书籍,点击右上角新建一本吧。
            </Typography.Text>
          </Col>
        )}
      </Row>

      <Modal
        title="新建书籍"
        open={open}
        onOk={onCreate}
        onCancel={() => setOpen(false)}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" initialValues={{ target_words: 100000 }}>
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
