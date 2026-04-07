import React from "react";
import { Button, Input, Select, Space, Table, Typography } from "antd";
import type { ReplaceRuleRow } from "../../app/domains/rules/replaceRows";

const { Text } = Typography;

export function ReplaceRulesEditor(props: {
  rows: ReplaceRuleRow[];
  onAddRow: (stage?: "asr" | "en") => void;
  onUpdateRow: (id: string, patch: Partial<ReplaceRuleRow>) => void;
  onRemoveRow: (id: string) => void;
  extraHint?: React.ReactNode;
  addLabel?: string;
}) {
  const rows = props.rows || [];
  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      {props.extraHint ? <Text type="secondary">{props.extraHint}</Text> : null}
      <Table
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: "要替换的内容",
            dataIndex: "src",
            render: (v: string, r: ReplaceRuleRow) => (
              <Input value={v} placeholder="例如：王大锤 / 错别字" onChange={(e) => props.onUpdateRow(r.id, { src: e.target.value })} />
            ),
          },
          {
            title: "替换成",
            dataIndex: "tgt",
            render: (v: string, r: ReplaceRuleRow) => (
              <Input
                value={v}
                placeholder={r.stage === "asr" ? "识别纠错必须填写" : "可选：统一写法"}
                onChange={(e) => props.onUpdateRow(r.id, { tgt: e.target.value })}
              />
            ),
          },
          {
            title: "用途",
            dataIndex: "stage",
            width: 180,
            render: (v: any, r: ReplaceRuleRow) => (
              <Select
                value={String(v || "asr")}
                style={{ width: "100%" }}
                options={[
                  { label: "识别纠错（错字→正字）", value: "asr" },
                  { label: "英文替换（整词→整词）", value: "en" },
                ]}
                onChange={(vv) => props.onUpdateRow(r.id, { stage: vv as any })}
              />
            ),
          },
          {
            title: "操作",
            dataIndex: "op",
            width: 70,
            render: (_: any, r: ReplaceRuleRow) => (
              <Button size="small" danger onClick={() => props.onRemoveRow(r.id)}>
                删除
              </Button>
            ),
          },
        ]}
      />
      <Space wrap>
        <Button onClick={() => props.onAddRow("asr")}>{props.addLabel || "添加一条"}</Button>
        <Button onClick={() => props.onAddRow("en")}>添加“英文替换”</Button>
      </Space>
    </Space>
  );
}

