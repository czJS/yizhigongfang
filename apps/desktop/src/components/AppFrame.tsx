import React from "react";
import { Button, Layout, Menu, Space, Typography } from "antd";
import {
  AppstoreOutlined,
  HistoryOutlined,
  KeyOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  RocketOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";

const { Sider } = Layout;
const { Title, Text } = Typography;

export function AppFrame(props: {
  route: "wizard" | "workbench" | "history" | "mode" | "rules" | "advanced" | "system";
  setRoute: (r: any) => void;
  mode: "lite" | "quality" | "online";
  taskCreationBlocked?: boolean;
  siderCollapsed: boolean;
  setSiderCollapsed: (updater: (v: boolean) => boolean) => void;
  screens: {
    wizard: React.ReactNode;
    workbench: React.ReactNode;
    history: React.ReactNode;
    modeSelect: React.ReactNode;
    rulesCenter: React.ReactNode;
    advanced: React.ReactNode;
    system: React.ReactNode;
  };
  extras?: React.ReactNode;
}) {
  const { route, setRoute, mode, taskCreationBlocked, siderCollapsed, setSiderCollapsed, screens, extras } = props;
  const main =
    route === "wizard"
      ? screens.wizard
      : route === "workbench"
        ? screens.workbench
        : route === "history"
          ? screens.history
          : route === "mode"
            ? screens.modeSelect
            : route === "rules"
              ? screens.rulesCenter
              : route === "advanced"
                ? screens.advanced
                : screens.system;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={220}
        theme="light"
        style={{ borderRight: "1px solid #f0f0f0" }}
        collapsible
        collapsed={siderCollapsed}
        trigger={null}
        onCollapse={(v) => setSiderCollapsed(() => v)}
      >
        <div style={{ padding: "16px 16px 8px 16px" }}>
          <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
            {!siderCollapsed ? (
              <Title level={5} style={{ margin: 0 }}>
                <Space size={6}>
                  <RocketOutlined />
                  秒译出海
                </Space>
              </Title>
            ) : (
              <Title level={5} style={{ margin: 0 }}>
                <RocketOutlined />
              </Title>
            )}
            <Button
              size="small"
              type="text"
              icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setSiderCollapsed((v) => !v)}
            />
          </Space>
          {!siderCollapsed && <Text type="secondary"></Text>}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[route]}
          onClick={(e) => setRoute(e.key as any)}
          items={[
            { key: "mode", icon: <ThunderboltOutlined />, label: "模式选择" },
            { key: "wizard", icon: <PlusOutlined />, label: "新建任务", disabled: !!taskCreationBlocked },
            { key: "workbench", icon: <AppstoreOutlined />, label: "任务中心" },
            { key: "history", icon: <HistoryOutlined />, label: "历史记录" },
            ...(mode === "lite" || mode === "quality" ? [{ key: "rules", icon: <KeyOutlined />, label: "规则中心" }] : []),
            { key: "advanced", icon: <SettingOutlined />, label: "高级设置" },
            { key: "system", icon: <SettingOutlined />, label: "系统" },
          ]}
        />
      </Sider>
      <Layout>{main}</Layout>
      {extras}
    </Layout>
  );
}

