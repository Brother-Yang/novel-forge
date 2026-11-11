import { Descriptions, Typography } from "antd";
import type { CharacterState } from "../api/types";
import { characterStateFields } from "../constants/narrative";

interface CharacterFactsProps {
  state: CharacterState | undefined;
}

export default function CharacterFacts({ state }: CharacterFactsProps) {
  const items = characterStateFields
    .filter(({ key }) => state?.[key])
    .map(({ key, label }) => ({ key, label, children: state?.[key] }));

  return (
    <section aria-label="人物正文进展" style={{ marginBottom: 12 }}>
      {items.length > 0 ? (
        <Descriptions title="正文中的最新状态" size="small" column={2} items={items} />
      ) : (
        <Typography.Text type="secondary">尚无已同步的正文事实。</Typography.Text>
      )}
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        状态来自已保存正文，修订或调整章节顺序后重新汇总。人物卡保留作者设定。
      </Typography.Paragraph>
    </section>
  );
}
