import React from "react";
import { Alert, Card, Layout, Space, Tabs, Typography } from "antd";
import { useRulesCenterCtx } from "../app/contexts/RulesCenterContext";
import { RulePairsEditor } from "../components/rules/RulePairsEditor";

const { Content } = Layout;
const { Text } = Typography;

export function RulesCenterScreen() {
  const {
    rulesError,
    rulesLoading,
    onOpenRules,
    onSaveGlobalRules,
    globalReplaceRows,
    onAddGlobalReplaceRow,
    onRemoveGlobalReplaceRow,
    onUpdateGlobalReplaceRow,
  } = useRulesCenterCtx();

  const didInitRef = React.useRef(false);

  React.useEffect(() => {
    // Auto load on first enter; avoid double-run in dev strict mode.
    if (didInitRef.current) return;
    didInitRef.current = true;
    onOpenRules();
  }, [onOpenRules]);

  // Best-effort flush when leaving this screen, to avoid losing edits if user navigates away quickly.
  React.useEffect(() => {
    return () => {
      try {
        onSaveGlobalRules(undefined, { silent: true });
      } catch {
        // ignore
      }
    };
  }, [onSaveGlobalRules]);

  return (
    <Content style={{ padding: 16 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card
          title="用词与纠错"
          extra={<Text type="secondary">全局规则会自动保存</Text>}
        >
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="全局规则：默认对轻量 / 质量的新任务生效。"
              description="这里维护的是项目级规则中心，适合放常见识别纠错、固定术语和英文统一写法。"
            />
            {!!rulesError && <Alert type="warning" showIcon message="暂时连不上后端" description={rulesError} />}
          </Space>
        </Card>

        <Tabs
          items={[
            {
              key: "global",
              label: "全局规则",
              children: (
                <Space direction="vertical" size="large" style={{ width: "100%" }}>
                  <Card size="small">
                    <RulePairsEditor
                      title="中文 → 中文（识别纠错）"
                      hint="适合：把错别字/常见误识别改成你想要的写法。"
                      rows={globalReplaceRows}
                      stage="asr"
                      onAdd={onAddGlobalReplaceRow}
                      onRemove={onRemoveGlobalReplaceRow}
                      onUpdate={onUpdateGlobalReplaceRow}
                    />
                  </Card>
                  <Card size="small">
                    <RulePairsEditor
                      title="英文 → 英文（英文替换）"
                      hint="适合：把某个英文整词替换成另一种写法（更稳，不乱改）。"
                      rows={globalReplaceRows}
                      stage="en"
                      onAdd={onAddGlobalReplaceRow}
                      onRemove={onRemoveGlobalReplaceRow}
                      onUpdate={onUpdateGlobalReplaceRow}
                    />
                  </Card>
                </Space>
              ),
            },
          ]}
        />
      </Space>
    </Content>
  );
}
