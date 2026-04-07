// @vitest-environment jsdom

import React, { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppOverlays } from "./AppOverlays";

vi.mock("./modals/GlossaryModal", () => ({ GlossaryModal: () => null }));
vi.mock("./modals/CleanupArtifactsModal", () => ({ CleanupArtifactsModal: () => null }));
vi.mock("./modals/EraseSubOverrideModal", () => ({ EraseSubOverrideModal: () => null }));
vi.mock("./modals/RegionPickerModal", () => ({ RegionPickerModal: () => null }));

describe("AppOverlays auth gate", () => {
  it("renders logo and updated auth guidance copy", () => {
    render(
      <AppOverlays
        mode="lite"
        regionPickerFileInputRef={createRef<HTMLInputElement>()}
        handleRegionPickerFileChange={() => {}}
        glossary={{ open: false, loading: false, error: "", items: [], closeModal: () => {}, openModal: () => {}, downloadJson: () => {}, fillExample: () => {}, save: async () => {}, updateRow: () => {}, removeRow: () => {}, addRow: () => {} }}
        cleanupDialogOpen={false}
        cleanupIncludeDiagnostics={false}
        cleanupIncludeResume={false}
        cleanupIncludeReview={false}
        setCleanupIncludeDiagnostics={() => {}}
        setCleanupIncludeResume={() => {}}
        setCleanupIncludeReview={() => {}}
        confirmCleanupArtifacts={async () => {}}
        setCleanupDialogOpen={() => {}}
        overrideModalOpen={false}
        overrideEditing={null}
        wizardTasks={[]}
        batchesRef={{ current: [] }}
        overrideForm={{ setFieldsValue: () => {}, validateFields: async () => ({}) } as any}
        setOverrideModalOpen={() => {}}
        setOverrideEditing={() => {}}
        applyEraseSubOverrideToWizard={() => {}}
        applyEraseSubOverrideToBatch={() => {}}
        openRegionPickerFor={() => {}}
        openRegionPicker={() => {}}
        currentOverrideLocalPath={() => ""}
        regionPickerPurpose="erase"
        regionPickerOpen={false}
        setRegionPickerOpen={() => {}}
        onApplyRegionPicker={() => {}}
        regionPickerTarget="batch"
        regionPickerRect={{ x: 0, y: 0, w: 0, h: 0 }}
        setRegionRectSafe={() => {}}
        regionPickerSampleFontSize={36}
        setFinalSubtitleFontSize={() => {}}
        regionPickerSampleText=""
        setRegionPickerSampleText={() => {}}
        regionPickerVideoPath=""
        regionPickerVideoReady={false}
        regionPickerVideoError=""
        regionPickerVideoInfo={{}}
        regionPickerVideoRef={createRef<HTMLVideoElement>()}
        setRegionPickerVideoReady={() => {}}
        setRegionPickerVideoError={() => {}}
        setRegionPickerVideoInfo={() => {}}
        regionPickerVideoBox={{}}
        regionPickerVideoScale={1}
        resetRegionPickerVideo={() => {}}
        showAuthGate
        authStage="login"
        authApiBase="https://auth.miaoyichuhai.com"
        authEmail=""
        setAuthEmail={() => {}}
        authCode=""
        setAuthCode={() => {}}
        authActivationCode=""
        setAuthActivationCode={() => {}}
        authStatusText=""
        authError=""
        devCodeHint=""
        authUserEmail=""
        authLicenseStatus="none"
        authLicenseExpireAt=""
        authDeviceLimit={2}
        authActiveDeviceCount={0}
        authSendingCode={false}
        authSendCodeCooldownSeconds={0}
        authLoggingIn={false}
        authRedeemingCode={false}
        authLicenseLoading={false}
        handleAuthSendCode={async () => {}}
        handleAuthLogin={async () => {}}
        handleAuthRedeemCode={async () => {}}
        handleAuthLogout={async () => {}}
        showRenewalModal={false}
        canRenewInApp={false}
        closeRenewalModal={() => {}}
        requireLocalPacks={false}
        modelsReady
        modelsLoading={false}
        modelsZipHint=""
        missingLabels={[]}
        modelsImporting={false}
        handlePickModels={async () => {}}
        modelsRoot=""
        modelsError=""
        ollamaReady
        ollamaPortOpen
        ollamaZipHint=""
        ollamaImporting={false}
        ollamaStarting={false}
        handlePickOllama={async () => {}}
        handleEnsureOllama={async () => {}}
        ollamaRoot=""
        ollamaError=""
        ollamaLoading={false}
        refreshOllamaStatus={async () => {}}
      />,
    );

    expect(screen.getByAltText("秒译出海 Logo")).toBeTruthy();
    expect(screen.getByText("秒译出海")).toBeTruthy();
    expect(screen.getByText(/账号已开通过授权，可直接进入工作台/)).toBeTruthy();
    expect(screen.getByPlaceholderText("请输入激活码（已有授权可留空）")).toBeTruthy();
    expect(screen.queryByText("账号中心")).toBeNull();
  });
});
