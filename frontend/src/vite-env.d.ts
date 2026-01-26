/// <reference types="vite/client" />

interface Window {
  bridge?: {
    apiBase: string;
    selectDirectory: () => Promise<string>;
    getCwd: () => Promise<string>;
    getProjectRoot: () => Promise<string>;
    openPath: (targetPath: string) => Promise<{ ok: boolean; error?: string }>;
    ensureDir: (baseDir: string, relativeDir: string) => Promise<{ ok: boolean; path: string }>;
    writeFile: (baseDir: string, relativePath: string, bytes: Uint8Array | number[]) => Promise<{ ok: boolean; path: string; size: number }>;
    getMacAddresses: () => Promise<string[]>;
  };
}
