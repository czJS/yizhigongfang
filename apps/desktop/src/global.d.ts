export {};

declare global {
  interface Window {
    bridge?: {
      apiBase?: string;
      apiToken?: string;
      authRequest?: (payload: {
        method?: string;
        path: string;
        headers?: Record<string, string>;
        body?: unknown;
      }) => Promise<{
        ok: boolean;
        status: number;
        text?: string;
        data?: any;
      }>;
      runtimeInfo?: {
        platform: string;
        packaged: boolean;
        windowsPackagedProduct: boolean;
        productEdition?: string;
      };
      selectDirectory?: () => Promise<string>;
      getDefaultOutputsRoot?: () => Promise<string>;
      openPath?: (targetPath: string) => Promise<{ ok: boolean; error?: string }>;
      ensureDir?: (baseDir: string, relativeDir: string) => Promise<{ ok: boolean; path: string }>;
      writeFile?: (
        baseDir: string,
        relativePath: string,
        bytes: Uint8Array | number[],
      ) => Promise<{ ok: boolean; path: string; size: number }>;
      getDeviceCode?: () => Promise<string>;
      getDeviceIdentity?: () => Promise<{ deviceCode: string; aliases?: string[] }>;
      getRuntimeStatus?: () => Promise<{
        packagedWindowsProduct: boolean;
        manifestPath?: string;
        manifestExists?: boolean;
        backendExe: string;
        backendExeExists: boolean;
        qualityWorkerExe: string;
        qualityWorkerExeExists: boolean;
        packagedConfigPath: string;
        packagedConfigExists: boolean;
        modelsPackZip: string;
        modelsPackZipExists: boolean;
        ollamaPackZip: string;
        ollamaPackZipExists: boolean;
        ollamaRoot: string;
        ollamaExe: string;
        ollamaExeExists: boolean;
        ollamaModelsRoot: string;
        runtimeChecks?: { key?: string; label?: string; path?: string; exists?: boolean }[];
      }>;
      restartBackend?: () => Promise<{ ok: boolean; apiBase?: string; port?: number; error?: string }>;
      collectDiagnosticsBundle?: () => Promise<{
        summary?: Record<string, any>;
        files?: { name: string; content: string }[];
      }>;

      getModelStatus?: () => Promise<{
        ready: boolean;
        root: string;
        zip?: string;
        zipHint?: string;
        layout?: string;
        manifestPath?: string;
        missing?: { key?: string; label?: string; path?: string }[];
      }>;
      pickModelPack?: () => Promise<string>;
      extractModelPack?: (zipPath: string) => Promise<{
        ok: boolean;
        root?: string;
        zip?: string;
        zipHint?: string;
        missing?: { key?: string; label?: string; path?: string }[];
        error?: string;
      }>;

      getOllamaStatus?: () => Promise<{
        ready: boolean;
        root: string;
        modelsRoot: string;
        portOpen: boolean;
        zip?: string;
        zipHint?: string;
        requiredModels?: string[];
        installedModels?: string[];
        missingModels?: string[];
        activeModelCount?: number;
        activeModels?: string[];
        processorSummary?: string;
        acceleration?: string;
        usesGpu?: boolean;
      }>;
      ensureOllama?: () => Promise<{
        ok: boolean;
        ready?: boolean;
        root?: string;
        modelsRoot?: string;
        portOpen?: boolean;
        requiredModels?: string[];
        installedModels?: string[];
        missingModels?: string[];
        activeModelCount?: number;
        activeModels?: string[];
        processorSummary?: string;
        acceleration?: string;
        usesGpu?: boolean;
        error?: string;
      }>;
      pickOllamaPack?: () => Promise<string>;
      extractOllamaPack?: (zipPath: string) => Promise<{
        ok: boolean;
        root?: string;
        modelsRoot?: string;
        zip?: string;
        zipHint?: string;
        portOpen?: boolean;
        error?: string;
      }>;
    };
  }
}


