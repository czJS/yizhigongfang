import React from "react";
import { Alert, Button, Card, Col, Layout, Row, Space, Tag, Tooltip, Typography, message } from "antd";
import { CloudOutlined, CrownOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useModeSelectCtx } from "../app/contexts/ModeSelectContext";

const { Content } = Layout;
const { Paragraph, Text, Title } = Typography;

export function ModeSelectScreen() {
  const { availableModes, mode, config, uiPrefs, setMode, setUiPrefs, saveUiPrefs } = useModeSelectCtx();
  const qualityOnly = Boolean((config as any)?.ui?.quality_only);
  const qualityTeaserOnly = Boolean((config as any)?.ui?.quality_teaser_only);
  const onlineDisabled = Boolean((config as any)?.ui?.online_disabled);
  const [selectedCard, setSelectedCard] = React.useState<"lite" | "quality" | "online">(mode);

  React.useEffect(() => {
    if (!(qualityTeaserOnly && selectedCard === "quality")) {
      setSelectedCard(mode);
    }
  }, [mode, qualityTeaserOnly, selectedCard]);

  const cards = [
    {
      key: "lite" as const,
      title: "轻量模式",
      icon: <ThunderboltOutlined style={{ fontSize: 22, color: "#1677ff" }} />,
      desc: "本地离线，资源占用低，适合快速起步与稳定交付。",
    },
    {
      key: "quality" as const,
      title: "质量模式",
      icon: <CrownOutlined style={{ fontSize: 22, color: "#722ed1" }} />,
      desc: qualityTeaserOnly ? "当前版本仅展示能力介绍与升级入口。" : "更高质量（更慢），对算力要求更高。",
    },
    {
      key: "online" as const,
      title: "在线模式",
      icon: <CloudOutlined style={{ fontSize: 22, color: "#13c2c2" }} />,
      desc: "依赖在线服务与密钥，适合特定联网场景。",
    },
  ];

  function renderModeDetail() {
    if (selectedCard === "quality" && qualityTeaserOnly) {
      return (
        <Card title="质量模式介绍" style={{ marginTop: 16 }}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="当前版本的质量模式为介绍页，不会切入真实质量链路。"
              description="你现在仍在使用轻量模式。质量卡片只用于展示更高阶能力与升级入口，避免轻量产品误切到重型工作流。"
            />
            <div>
              <Title level={5} style={{ marginTop: 0 }}>为什么推荐升级到质量模式</Title>
              <Paragraph style={{ marginBottom: 8 }}>
                质量模式的核心价值，不是“多几个按钮”，而是把轻量模式里原本需要人工返工的环节尽量前置自动化。
                对需要稳定交付、减少人工审校时间的团队，质量模式通常更省总成本。
              </Paragraph>
              <Paragraph style={{ marginBottom: 0 }}>
                轻量模式是钩子产品，适合先跑通流程、先出结果；质量模式则面向更高成片要求，重点提升字幕可读性、翻译稳定性、配音自然度和最终交付确定性。
              </Paragraph>
            </div>
            <Row gutter={[12, 12]}>
              <Col span={12}>
                <Card size="small" title="质量模式更强的地方">
                  <Space direction="vertical" size={6}>
                    <Text>1. 更强的字幕优化：自动处理长句、密句、阅读速度过快等问题。</Text>
                    <Text>2. 更稳的翻译质量：针对专名、短语、上下文更容易做约束和修正。</Text>
                    <Text>3. 更自然的配音结果：更重视节奏规划、贴时长和最终听感。</Text>
                    <Text>4. 更省人工：把原本要手动审校、手动返工的部分尽量提前自动完成。</Text>
                  </Space>
                </Card>
              </Col>
              <Col span={12}>
                <Card size="small" title="轻量模式适合先体验">
                  <Space direction="vertical" size={6}>
                    <Text>1. 本地离线、资源占用低，上手快。</Text>
                    <Text>2. 适合先验证素材、流程和基础成片能力。</Text>
                    <Text>3. 适合预算敏感或只需“先出片”的场景。</Text>
                    <Text strong>4. 当你开始在意“更好读、更自然、更少返工”时，就该升级到质量模式。</Text>
                  </Space>
                </Card>
              </Col>
            </Row>
            <Card size="small" title="销售联系占位">
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Text>占位文案：这里放销售二维码 / 企业微信 / 飞书名片 / 试用咨询入口。</Text>
                <Text type="secondary">建议 CTA：联系销售开通质量模式，获取更高质量字幕、更自然配音和更低人工审校成本。</Text>
                <Button type="primary" disabled>
                  联系销售开通（占位）
                </Button>
              </Space>
            </Card>
          </Space>
        </Card>
      );
    }

    if (selectedCard === "lite") {
      return (
        <Card title="轻量模式说明" style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 0 }}>
            轻量模式会直接进入可运行、低资源占用的离线链路。它更强调“先跑通、先交付、先验证素材”，适合日常快速处理和机器资源有限的环境。
          </Paragraph>
        </Card>
      );
    }

    if (selectedCard === "online") {
      return (
        <Card title="在线模式说明" style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 0 }}>
            在线模式依赖外部服务和密钥，适合有稳定联网条件且明确需要在线能力的场景。若你主要追求本地稳定性，建议优先使用轻量模式。
          </Paragraph>
        </Card>
      );
    }

    return null;
  }

  return (
    <Content style={{ padding: 16 }}>
      <Card title="模式选择">
        <Row gutter={[12, 12]}>
          {cards.map((m) => {
            const forceDisabled = (qualityOnly && m.key !== "quality") || (onlineDisabled && m.key === "online");
            const available = availableModes.includes(m.key) && !forceDisabled;
            const selected = selectedCard === m.key;
            const current = mode === m.key;
            const reasons = (config as any)?.available_modes_detail?.[m.key]?.reasons as string[] | undefined;
            const reasonText =
              onlineDisabled && m.key === "online"
                ? "在线模式暂未开放"
                : forceDisabled
                ? "此版本仅支持质量模式"
                : Array.isArray(reasons) && reasons.length > 0
                  ? reasons.join("\n")
                  : "";
            return (
              <Col key={m.key} span={8}>
                <Card
                  hoverable={available && !forceDisabled}
                  style={{
                    borderColor: selected ? "#1677ff" : undefined,
                    opacity: available && !forceDisabled ? 1 : 0.5,
                    cursor: available && !forceDisabled ? "pointer" : "not-allowed",
                    height: "100%",
                  }}
                  onClick={() => {
                    if (!available || forceDisabled) return;
                    setSelectedCard(m.key);
                    if (qualityTeaserOnly && m.key === "quality") {
                      message.info("当前版本的质量模式仅展示介绍，不会切入真实质量链路。");
                      return;
                    }
                    setMode(m.key);
                    const next = { ...uiPrefs, defaultMode: m.key };
                    setUiPrefs(next);
                    saveUiPrefs(next);
                    message.success(`已选择：${m.title}`);
                  }}
                >
                  <Space direction="vertical" size="small" style={{ width: "100%" }}>
                    <Space align="center" style={{ justifyContent: "space-between", width: "100%" }}>
                      <Space align="center">
                        {m.icon}
                        <Text strong>{m.title}</Text>
                      </Space>
                      <Space>
                        {selected && <Tag color="blue">已选</Tag>}
                        {current && <Tag color="green">当前运行</Tag>}
                        {qualityTeaserOnly && m.key === "quality" && <Tag color="purple">介绍</Tag>}
                        {onlineDisabled && m.key === "online" && <Tag color="default">暂未开放</Tag>}
                      </Space>
                      {(!available || forceDisabled) &&
                        (reasonText ? (
                          <Tooltip title={<pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{reasonText}</pre>}>
                            <Tag>不可用</Tag>
                          </Tooltip>
                        ) : (
                          <Tag>不可用</Tag>
                        ))}
                    </Space>
                    <Text type="secondary">{m.desc}</Text>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>
      {renderModeDetail()}
    </Content>
  );
}

