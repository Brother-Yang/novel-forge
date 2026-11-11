import { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, List, Space, Spin, Tag, Typography } from "antd";
import { api } from "../api";
import type { PlotThread } from "../api/types";
import { plotThreadStatuses } from "../constants/narrative";

interface PlotThreadPanelProps {
  bookId: string;
}

function BookPlotThreadPanel({ bookId }: PlotThreadPanelProps) {
  const [threads, setThreads] = useState<PlotThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    api.listPlotThreads(bookId).then(
      (data) => {
        if (!active) return;
        setThreads(data);
        setError("");
        setLoading(false);
      },
      (err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "剧情进展加载失败");
        setLoading(false);
      }
    );
    return () => {
      active = false;
    };
  }, [bookId, refresh]);

  const reload = () => {
    setLoading(true);
    setRefresh((value) => value + 1);
  };

  return (
    <Card
      title="剧情进展"
      extra={
        <Button aria-label="刷新剧情进展" loading={loading} disabled={loading} onClick={reload}>
          刷新
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        根据已保存正文追踪伏笔与剧情线程。正文修订后会重新汇总；同步失败可在生成面板修复记忆。
      </Typography.Paragraph>
      {error && (
        <Alert
          type="error"
          message={error}
          action={
            <Button disabled={loading} onClick={reload}>
              重试
            </Button>
          }
        />
      )}
      {loading && <Spin aria-label="加载剧情进展" />}
      {!loading && !error && threads.length === 0 && <Empty description="尚未创建剧情线程" />}
      <List
        dataSource={threads}
        rowKey="id"
        renderItem={(thread) => {
          const status = plotThreadStatuses[thread.derived_status ?? thread.status];
          return (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <Typography.Text strong>{thread.name}</Typography.Text>
                    <Tag color={status?.color}>{status?.label ?? thread.status}</Tag>
                  </Space>
                }
                description={
                  <>
                    <Typography.Paragraph>{thread.description}</Typography.Paragraph>
                    <Typography.Text type="secondary">
                      {thread.derived_status ? "依据正文事实" : "依据剧情计划，尚无正文事实更新"}
                    </Typography.Text>
                  </>
                }
              />
            </List.Item>
          );
        }}
      />
    </Card>
  );
}

export default function PlotThreadPanel({ bookId }: PlotThreadPanelProps) {
  return <BookPlotThreadPanel key={bookId} bookId={bookId} />;
}
