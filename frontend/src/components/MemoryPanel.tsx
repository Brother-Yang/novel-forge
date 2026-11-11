import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, List, Space, Spin, Tag, Typography } from "antd";
import { api } from "../api";
import type { MemoryStatus } from "../api/types";

interface MemoryPanelProps {
  bookId: string;
}

const sourceLabels = {
  worldbuilding: "世界观设定",
  chapter_summary: "章节摘要",
  volume_summary: "卷摘要",
  chapter_facts: "章节事实",
};
const statusLabels = { pending: "等待同步", running: "正在同步", error: "同步失败" };

function BookMemoryPanel({ bookId }: MemoryPanelProps) {
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [repairing, setRepairing] = useState(false);
  const repairingRef = useRef(false);
  const requestVersion = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      if (repairingRef.current) return;
      const version = ++requestVersion.current;
      api.getMemoryStatus(bookId).then(
        (data) => {
          if (cancelled || version !== requestVersion.current) return;
          setStatus(data);
          setError("");
          if (data.pending + data.running > 0) timer = setTimeout(load, 2500);
        },
        (err: unknown) => {
          if (cancelled || version !== requestVersion.current) return;
          setError(err instanceof Error ? err.message : "记忆状态加载失败");
        }
      );
    };
    if (!repairingRef.current) load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      requestVersion.current += 1;
    };
  }, [bookId, refresh]);

  const repair = async () => {
    if (repairingRef.current) return;
    repairingRef.current = true;
    setRepairing(true);
    // 使修复前已发出的查询失效,避免旧结果把新状态覆盖。
    const version = ++requestVersion.current;
    let submitted = false;
    try {
      const result = await api.repairMemory(bookId);
      if (version !== requestVersion.current) return;
      setStatus(result);
      setError("");
      submitted = true;
    } catch (err) {
      if (version === requestVersion.current) {
        setError(err instanceof Error ? err.message : "提交记忆修复失败");
      }
    } finally {
      if (version === requestVersion.current) {
        repairingRef.current = false;
        setRepairing(false);
        if (submitted) setRefresh((value) => value + 1);
      }
    }
  };

  return (
    <Card title="创作记忆" style={{ marginBottom: 16 }}>
      <Typography.Paragraph type="secondary">
        记忆用于后续章节检索。修复会补齐摘要、人物与剧情事实和索引，保留已保存的正文。
      </Typography.Paragraph>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button disabled={repairing} onClick={() => setRefresh((v) => v + 1)}>
              重试查询
            </Button>
          }
          style={{ marginBottom: 12 }}
        />
      )}
      {!status && !error && <Spin aria-label="加载记忆状态" />}
      {status && (
        <>
          <Space wrap>
            <Tag color="green">
              可用 {status.ready} / {status.total}
            </Tag>
            <Tag>等待 {status.pending}</Tag>
            <Tag color="processing">处理中 {status.running}</Tag>
            <Tag color={status.failed ? "error" : "default"}>失败 {status.failed}</Tag>
            <Tag color={status.missing ? "warning" : "default"}>缺失 {status.missing}</Tag>
            <Button
              loading={repairing}
              disabled={status.failed + status.missing === 0 || repairing}
              onClick={() => void repair()}
            >
              修复缺失或失败的记忆
            </Button>
            <Button disabled={repairing} onClick={() => setRefresh((v) => v + 1)}>
              刷新状态
            </Button>
          </Space>
          {status.items.length > 0 && (
            <List
              size="small"
              dataSource={status.items}
              rowKey="id"
              renderItem={(item) => (
                <List.Item>
                  <Space wrap>
                    <Typography.Text>{sourceLabels[item.source_type]}</Typography.Text>
                    <Tag>{statusLabels[item.status]}</Tag>
                    {item.error && <Typography.Text type="danger">{item.error}</Typography.Text>}
                  </Space>
                </List.Item>
              )}
            />
          )}
        </>
      )}
    </Card>
  );
}

export default function MemoryPanel({ bookId }: MemoryPanelProps) {
  return <BookMemoryPanel key={bookId} bookId={bookId} />;
}
