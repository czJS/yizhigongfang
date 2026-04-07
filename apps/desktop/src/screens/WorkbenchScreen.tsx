import React from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Drawer,
  Layout,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { FolderOpenOutlined } from "@ant-design/icons";
import type { BatchModel, BatchTask, UiTaskState } from "../batchTypes";
import { TaskDrawerContent } from "../components/taskDrawer/TaskDrawerContent";
import { UnifiedReviewDrawer } from "../components/unifiedReview/UnifiedReviewDrawer";
import { twoDigitIndex } from "../utils";
import { tagColorForUiState } from "../app/appHelpers";
import { batchStateLabel, modeLabel, taskStateLabel } from "../app/labels";
import { useWorkbenchCtx } from "../app/contexts/WorkbenchContext";

const { Content } = Layout;
const { Text } = Typography;

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function StageProgress(props: { stage?: number; state: UiTaskState }) {
  const total = 8;
  const stage0 = typeof props.stage === "number" && Number.isFinite(props.stage) ? props.stage : props.state === "completed" ? total : 0;
  const stage = clamp(Math.floor(stage0), 0, total);
  const done = props.state === "completed";
  const filledCount = done ? total : Math.max(0, stage - 1);
  const currentStage = done || stage <= 0 ? null : stage;
  const label = done ? `${total}/${total}` : `${stage}/${total}`;

  return (
    <Space size={8} align="center">
      <Text type="secondary">{label}</Text>
      <div className="ygf-stagebar" aria-label={`progress ${label}`}>
        {Array.from({ length: total }).map((_, i) => {
          const idx = i + 1;
          const filled = idx <= filledCount;
          const active = currentStage === idx && (props.state === "running" || props.state === "paused");
          const cls = `ygf-stagebar-cell ${filled ? "is-filled" : ""} ${active ? "is-active" : ""}`;
          return <span key={idx} className={cls} />;
        })}
      </div>
    </Space>
  );
}

export function WorkbenchScreen() {
  const {
    batches,
    activeBatchId,
    batchCounts,
    onNewBatch,
    onSetActiveBatchId,
    onOpenTaskDrawer,
    onOpenBatchOutputFolder,
    onOpenPath,
    onOpenDefaultOutputsFolder,
    onDeliverTaskToOutputDir,
    onPauseQueue,
    onResumeQueue,
    onStartQueue,
    safeStem,
    drawerOpen,
    drawerTaskIndex,
    drawerWidth,
    drawerInitialTab,
    drawerLog,
    drawerLogLoading,
    activeBatch,
    qualityGates,
    isDockerDev,
    onCloseDrawer,
    onResumeTaskInPlace,
    onRunReviewAndPoll,
    onApplyReviewAndRefresh,
    onExportDiagnosticZipForTask,
    onOpenCleanupDialog,
    onOpenQualityUpgradeWizardFromTask,
    onGoSystem,
    showTaskLogs,
    onCancelTaskInBatch,
    onRestartTaskInBatch,
    onArchiveBatch,
    uiPrefs,
    onSetUiPrefs,
    onSaveUiPrefs,
    taskCreationBlocked,
    taskCreationBlockReason,
    canRenewInApp,
    openRenewalModal,
  } = useWorkbenchCtx();

  const [unifiedReviewOpen, setUnifiedReviewOpen] = React.useState(false);
  const [unifiedReviewBatchId, setUnifiedReviewBatchId] = React.useState<string>("");
  const [unifiedReviewTaskId, setUnifiedReviewTaskId] = React.useState<string>("");
  const [archiveConfirmBatchId, setArchiveConfirmBatchId] = React.useState<string>("");
  const [skipArchiveConfirmDraft, setSkipArchiveConfirmDraft] = React.useState(false);
  const skipArchiveConfirm = Boolean(uiPrefs?.skipArchiveConfirm);

  function handleArchiveBatch(batchId: string) {
    if (skipArchiveConfirm) {
      onArchiveBatch?.(batchId);
      return;
    }
    setSkipArchiveConfirmDraft(false);
    setArchiveConfirmBatchId(batchId);
  }

  function handleArchiveConfirm(batchId: string) {
    if (skipArchiveConfirmDraft) {
      const next = { ...(uiPrefs || {}), skipArchiveConfirm: true };
      onSetUiPrefs?.(next);
      onSaveUiPrefs?.(next);
    }
    setArchiveConfirmBatchId("");
    setSkipArchiveConfirmDraft(false);
    onArchiveBatch?.(batchId);
  }

  function handleArchiveCancel() {
    setArchiveConfirmBatchId("");
    setSkipArchiveConfirmDraft(false);
  }

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
      {batches.length === 0 ? (
        <Card>
          <Alert
            type="info"
            showIcon
            message="还没有任务"
            description="点击「新建任务」开始。"
            action={
              <Button type="primary" disabled={taskCreationBlocked} onClick={onNewBatch}>
                新建任务
              </Button>
            }
          />
        </Card>
      ) : (
        <Card title="任务中心">
          <Collapse
            accordion={false}
            defaultActiveKey={activeBatchId ? [activeBatchId] : undefined}
            items={batches.map((b) => {
              const counts = batchCounts(b);
              const runningIdx = b.tasks.findIndex((t) => t.state === "running");
              const current = runningIdx >= 0 ? b.tasks[runningIdx] : null;
              // Single-video only: no batch-level review entry.

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
                  title: "进度",
                  dataIndex: "progress",
                  width: 160,
                  render: (_: any, t: BatchTask) =>
                    t.taskId && ["running", "paused", "completed", "failed", "cancelled"].includes(t.state) ? (
                      <StageProgress stage={t.stage} state={t.state} />
                    ) : (
                      <Text type="secondary">-</Text>
                    ),
                },
                {
                  title: "操作",
                  key: "actions",
                  width: 300,
                  render: (_: any, t: BatchTask) => (
                    <Space>
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          onSetActiveBatchId(b.id);
                          onOpenTaskDrawer(b.tasks.findIndex((x) => x.index === t.index), "quality");
                        }}
                      >
                        详情
                      </Button>
                      {t.taskId && t.state === "paused" ? (
                        <Button
                          size="small"
                          type="link"
                          className="ygf-review-tab-blink"
                          onClick={() => {
                            onSetActiveBatchId(b.id);
                            // 翻译前“审核”应进入统一审核抽屉，而不是任务详情里的旧页签。
                            setUnifiedReviewBatchId(String(b.id));
                            setUnifiedReviewTaskId(String(t.taskId || ""));
                            setUnifiedReviewOpen(true);
                          }}
                        >
                          审核
                        </Button>
                      ) : null}
                      {/* 收敛：操作区不再展示“原因/校审”快捷入口（与详情重复且容易误解）。 */}
                      {t.taskId && ["running", "paused"].includes(t.state) ? (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => onCancelTaskInBatch?.(b.id, b.tasks.findIndex((x) => x.index === t.index))}
                        >
                          终止
                        </Button>
                      ) : t.taskId && ["failed", "cancelled"].includes(t.state) ? (
                        <Button
                          size="small"
                          type="link"
                          disabled={taskCreationBlocked}
                          onClick={() => onRestartTaskInBatch?.(b.id, b.tasks.findIndex((x) => x.index === t.index))}
                        >
                          开始
                        </Button>
                      ) : null}
                      {t.state === "completed" ? (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => {
                            const relDir = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
                            if (t.deliveredDir) {
                              const base = b.outputDir || "";
                              if (base) return onOpenPath(`${base}/${t.deliveredDir}`);
                              return onOpenDefaultOutputsFolder(t.deliveredDir);
                            }
                            onDeliverTaskToOutputDir(b.id, b.tasks.findIndex((x) => x.index === t.index))
                              .then(() => {
                                if (b.outputDir) return onOpenPath(`${b.outputDir}/${relDir}`);
                                return onOpenDefaultOutputsFolder(relDir);
                              })
                              .catch(() => onOpenBatchOutputFolder(b));
                          }}
                        >
                          文件
                        </Button>
                      ) : null}
                    </Space>
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
                      <Tag>总 {counts.total}</Tag>
                      <Tag color="green">完成 {counts.done}</Tag>
                      {counts.failed > 0 && <Tag color="red">失败 {counts.failed}</Tag>}
                      {counts.pending > 0 && <Tag>待处理 {counts.pending}</Tag>}
                    </Space>
                    <Space wrap>
                      {null}
                      {(() => {
                        const allDone = b.tasks.every((t) => ["completed", "failed", "cancelled"].includes(t.state));
                        const label = allDone
                          ? "已完成"
                          : b.state === "running"
                            ? "暂停"
                            : b.state === "paused"
                              ? "继续"
                              : b.state === "queued"
                                ? "排队中"
                                : "开始";
                        const disabled = allDone || b.state === "queued" || taskCreationBlocked;
                        return (
                          <Button
                            size="small"
                            type="primary"
                            disabled={disabled}
                            onClick={(e) => {
                              e.stopPropagation();
                              onSetActiveBatchId(b.id);
                              if (disabled) return;
                              if (b.state === "running") return onPauseQueue(b.id);
                              if (b.state === "paused") return onResumeQueue(b.id);
                              return onStartQueue(b.id);
                            }}
                          >
                            {label}
                          </Button>
                        );
                      })()}
                      <Button
                        size="small"
                        icon={<FolderOpenOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenBatchOutputFolder(b);
                        }}
                      >
                        交付
                      </Button>
                      {b.tasks.every((t) => ["completed", "failed", "cancelled"].includes(t.state)) ? (
                        <Popconfirm
                          open={archiveConfirmBatchId === b.id}
                          title="清理该任务批次？"
                          description={
                            <Space direction="vertical" size={8}>
                              <Text>会从任务中心移除，但历史记录会继续保留。不会删除交付文件。</Text>
                              <Checkbox
                                checked={skipArchiveConfirmDraft}
                                onChange={(e) => setSkipArchiveConfirmDraft(Boolean(e.target.checked))}
                                onClick={(e) => e.stopPropagation()}
                              >
                                以后不再提示
                              </Checkbox>
                            </Space>
                          }
                          okText="清理"
                          cancelText="取消"
                          onConfirm={() => handleArchiveConfirm(b.id)}
                          onCancel={handleArchiveCancel}
                        >
                          <Button
                            size="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleArchiveBatch(b.id);
                            }}
                          >
                            清理
                          </Button>
                        </Popconfirm>
                      ) : null}
                    </Space>
                  </Space>
                ),
                children: (
                  <Card size="small" style={{ marginTop: 8 }}>
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Space wrap>
                        <Text type="secondary">创建时间：{new Date(b.createdAt).toLocaleString()}</Text>
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
        </Card>
      )}

      {null}

      <UnifiedReviewDrawer
        open={unifiedReviewOpen}
        batch={(batches || []).find((x: any) => String(x.id) === String(unifiedReviewBatchId)) || null}
        isDockerDev={isDockerDev}
        initialSelectedTaskId={unifiedReviewTaskId || ""}
        onClose={() => {
          setUnifiedReviewOpen(false);
          setUnifiedReviewBatchId("");
          setUnifiedReviewTaskId("");
        }}
        onRunReviewForTaskIndex={async (taskIndex0: number) => {
          if (unifiedReviewBatchId) onSetActiveBatchId(String(unifiedReviewBatchId));
          await onRunReviewAndPoll(taskIndex0, "chs");
        }}
      />

      <Drawer
        title={activeBatch && drawerTaskIndex >= 0 ? `任务详情：${activeBatch.tasks[drawerTaskIndex].inputName}` : "任务详情"}
        open={drawerOpen}
        onClose={onCloseDrawer}
        width={drawerWidth}
        destroyOnClose={false}
        className="ygf-scrollbars"
      >
        {activeBatch && drawerTaskIndex >= 0 ? (
          <TaskDrawerContent
            batch={activeBatch}
            taskIndex={drawerTaskIndex}
            initialTab={drawerInitialTab}
            onOpenOutput={(rel) => {
              if (rel) {
                if (activeBatch.outputDir) return onOpenPath(`${activeBatch.outputDir}/${rel}`);
                return onOpenDefaultOutputsFolder(rel);
              }
              return onOpenBatchOutputFolder(activeBatch);
            }}
            qualityGates={qualityGates}
            showLogs={!!showTaskLogs}
            logText={drawerLog}
            logLoading={drawerLogLoading}
            onResume={(resumeFrom) => onResumeTaskInPlace(drawerTaskIndex, resumeFrom)}
            onRunReview={(lang) => onRunReviewAndPoll(drawerTaskIndex, lang)}
            onApplyReview={(action, use) => onApplyReviewAndRefresh(drawerTaskIndex, action, use)}
            onExportDiagnostic={(opts) => onExportDiagnosticZipForTask(drawerTaskIndex, opts)}
            onCleanup={(idx) => onOpenCleanupDialog(idx)}
            onUpgradeToQuality={(task) => onOpenQualityUpgradeWizardFromTask(task)}
            onGoSystem={onGoSystem}
          />
        ) : (
          <Text type="secondary">未选择任务</Text>
        )}
      </Drawer>
    </Content>
  );
}
