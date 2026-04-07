import { useCallback, useMemo, useState } from "react";
import { message } from "antd";
import { getGlossary, putGlossary } from "../services/glossaryApi";
import { createId, downloadTextFile, joinList, splitList } from "../app/appHelpers";

type GlossaryRow = { id: string; src: string; tgt: string; aliases: string; forbidden: string; note: string };

function rowsFromGlossary(doc: any): GlossaryRow[] {
  const items = Array.isArray(doc?.items) ? doc.items : [];
  return items.map((it: any, idx: number) => ({
    id: String(it?.id || `t${String(idx + 1).padStart(4, "0")}`),
    src: String(it?.src || ""),
    tgt: String(it?.tgt || ""),
    aliases: joinList(Array.isArray(it?.aliases) ? it.aliases : []),
    forbidden: joinList(Array.isArray(it?.forbidden) ? it.forbidden : []),
    note: String(it?.note || ""),
  }));
}

function glossaryDocFromRows(rows: GlossaryRow[]) {
  return {
    version: 1,
    items: rows
      .filter((r) => r.src && r.src.trim())
      .map((r, idx) => ({
        id: r.id || `t${String(idx + 1).padStart(4, "0")}`,
        src: r.src.trim(),
        tgt: (r.tgt || "").trim(),
        aliases: splitList(r.aliases || ""),
        forbidden: splitList(r.forbidden || ""),
        note: (r.note || "").trim(),
        scope: "global",
      })),
  };
}

export function useGlossaryModal() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<GlossaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const openModal = useCallback(async () => {
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const res = await getGlossary();
      setItems(rowsFromGlossary(res));
    } catch (err: any) {
      setItems([]);
      setError(err?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const closeModal = useCallback(() => setOpen(false), []);

  const save = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const doc = glossaryDocFromRows(items);
      await putGlossary(doc);
      message.success("已保存纠错表");
    } catch (err: any) {
      message.error(err?.message || "保存失败");
    } finally {
      setLoading(false);
    }
  }, [items]);

  const updateRow = useCallback((id: string, patch: Partial<GlossaryRow>) => {
    setItems((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }, []);

  const addRow = useCallback(() => {
    setItems((prev) => [...prev, { id: createId(), src: "", tgt: "", aliases: "", forbidden: "", note: "" }]);
  }, []);

  const removeRow = useCallback((id: string) => {
    setItems((prev) => prev.filter((r) => r.id !== id));
  }, []);

  const fillExample = useCallback(() => {
    setItems([
      {
        id: createId(),
        src: "一只千年蚊子",
        tgt: "蚊子",
        aliases: "",
        forbidden: "",
        note: "示例：修正常见误识别",
      },
    ]);
  }, []);

  const downloadJson = useCallback(() => {
    downloadTextFile("glossary.json", JSON.stringify(glossaryDocFromRows(items), null, 2));
  }, [items]);

  const api = useMemo(
    () => ({
      open,
      items,
      loading,
      error,
      setItems,
      openModal,
      closeModal,
      save,
      updateRow,
      addRow,
      removeRow,
      fillExample,
      downloadJson,
    }),
    [open, items, loading, error, openModal, closeModal, save, updateRow, addRow, removeRow, fillExample, downloadJson],
  );

  return api;
}

