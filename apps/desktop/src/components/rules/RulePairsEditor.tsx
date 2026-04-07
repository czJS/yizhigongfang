import React from "react";
import { Button, Input, Space, Table, Typography } from "antd";
import type { ReplaceRuleRow } from "../../app/domains/rules/replaceRows";

const { Text } = Typography;

export function RulePairsEditor(props: {
  title: string;
  hint?: string;
  rows: ReplaceRuleRow[];
  stage: "asr" | "en";
  onAdd: (stage: "asr" | "en") => void;
  onUpdate: (id: string, patch: Partial<ReplaceRuleRow>) => void;
  onRemove: (id: string) => void;
  addLabel?: string;
}) {
  const rows = (props.rows || []).filter((r) => (r as any)?.stage === props.stage);
  const isAsr = props.stage === "asr";
  const isEn = props.stage === "en";

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Space direction="vertical" size={2}>
        <Text strong>{props.title}</Text>
        {props.hint ? <Text type="secondary">{props.hint}</Text> : null}
      </Space>
      <Table
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: isAsr ? "错字（中文）" : isEn ? "英文" : "中文",
            dataIndex: "src",
            render: (v: string, r: ReplaceRuleRow) => (
              <Input
                value={v}
                placeholder={isAsr ? "例如：常见错别字" : isEn ? "例如：color" : "例如：王大锤"}
                onChange={(e) => props.onUpdate(r.id, { src: e.target.value })}
              />
            ),
          },
          {
            title: isAsr ? "改成（中文）" : isEn ? "改成" : "英文",
            dataIndex: "tgt",
            render: (v: string, r: ReplaceRuleRow) => (
              <Input
                value={v}
                placeholder={isAsr ? "正确写法" : isEn ? "例如：colour" : "你希望的固定译法"}
                onChange={(e) => props.onUpdate(r.id, { tgt: e.target.value })}
              />
            ),
          },
          {
            title: "操作",
            dataIndex: "op",
            width: 80,
            render: (_: any, r: ReplaceRuleRow) => (
              <Button size="small" danger onClick={() => props.onRemove(r.id)}>
                删除
              </Button>
            ),
          },
        ]}
      />
      <div>
        <Button onClick={() => props.onAdd(props.stage)}>{props.addLabel || "添加一条"}</Button>
      </div>
    </Space>
  );
}

