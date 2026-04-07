import React from "react";
import { Alert, Button, Card, Collapse, Empty, Layout, Popconfirm, Space, Table, Tag, Typography } from "antd";
import type { BatchModel, BatchTask, UiTaskState } from "../batchTypes";
import { twoDigitIndex } from "../utils";
import { tagColorForUiState } from "../app/appHelpers";
import { batchStateLabel, modeLabel, taskStateLabel } from "../app/labels";
import { useHistoryCtx } from "../app/contexts/HistoryContext";

const { Content } = Layout;
const { Text } = Typography;

export function HistoryScreen() {
  const { batches, batchCounts, onNewBatch, onOpenBatchOutputFolder, onOpenDeliveredDirForTask, onDeleteBatch, taskCreationBlocked, taskCreationBlockReason, canRenewInApp, openRenewalModal } =
    useHistoryCtx();

  return (
    <Content style={{ padding: 16 }}>
      {taskCreationBlocked ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前账号处于只读限制"
          description={taskCreationBlockReason}
          action={
            canRenewInApp ? (
              <Button size="small" type="primary" onClick={openRenewalModal}>
                输入新激活码
              </Button>
            ) : undefined
          }
        />
      ) : null}
      <Card
        title="历史记录"
        extra={<Text type="secondary">批次列表会保存在本机（localStorage）。重启仍在；清理浏览器数据/重装应用会丢失。</Text>}
      >
        {batches.length === 0 ? (
          <Empty description="暂无历史记录">
            <Button type="primary" disabled={taskCreationBlocked} onClick={onNewBatch}>
              新建批次
            </Button>
          </Empty>
        ) : (
          <Collapse
            accordion={false}
            items={batches.map((b) => {
              const counts = batchCounts(b);
              const columns = [
                { title: "序号", dataIndex: "index", width: 70, render: (v: number) => <Tag>{twoDigitIndex(v)}</Tag> },
                { title: "文件", dataIndex: "inputName", ellipsis: true },
                {
                  title: "状态",
                  dataIndex: "state",
                  width: 110,
                  render: (s: UiTaskState) => <Tag color={tagColorForUiState(s)}>{taskStateLabel(s)}</Tag>,
                },
                {
                  title: "交付",
                  dataIndex: "deliveredDir",
                  width: 90,
                  render: (_: any, t: BatchTask) => (
                    <Button size="small" onClick={() => onOpenDeliveredDirForTask(b, t)}>
                      打开
                    </Button>
                  ),
                },
              ];
              return {
                key: b.id,
                label: (
                  <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
                    <Space wrap>
                      <Text strong>{b.name}</Text>
                      <Tag>{modeLabel(b.mode)}</Tag>
                      <Tag>{batchStateLabel(b.state)}</Tag>
                      {b.archivedAt ? <Tag color="blue">已清理</Tag> : null}
                      <Tag>总 {counts.total}</Tag>
                      <Tag color="green">完成 {counts.done}</Tag>
                      {counts.failed > 0 && <Tag color="red">失败 {counts.failed}</Tag>}
                      {counts.pending > 0 && <Tag>待处理 {counts.pending}</Tag>}
                    </Space>
                    <Space wrap>
                      <Button
                        size="small"
                        type="primary"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenBatchOutputFolder(b);
                        }}
                      >
                        打开
                      </Button>
                      <Popconfirm
                        title="删除该批次？"
                        description="仅删除本地记录，不会影响后端文件。"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={() => onDeleteBatch(b.id)}
                      >
                        <Button size="small" danger onClick={(e) => e.stopPropagation()}>
                          删除记录
                        </Button>
                      </Popconfirm>
                    </Space>
                  </Space>
                ),
                children: (
                  <Card size="small" style={{ marginTop: 8 }}>
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      <Space wrap>
                        <Text type="secondary">创建时间：{new Date(b.createdAt).toLocaleString()}</Text>
                        {b.archivedAt ? <Text type="secondary">清理时间：{new Date(b.archivedAt).toLocaleString()}</Text> : null}
                        <Text type="secondary">输出目录：{b.outputDir || "（未选择）"}</Text>
                      </Space>
                      <Table
                        size="small"
                        rowKey={(r: any) => String(r.index)}
                        pagination={false}
                        columns={columns as any}
                        dataSource={b.tasks as any}
                      />
                    </Space>
                  </Card>
                ),
              };
            })}
          />
        )}
      </Card>
    </Content>
  );
}

