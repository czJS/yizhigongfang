const TYPE_LABELS = {
  standard: "标准激活码",
  monthly: "月卡",
  trial: "试用码",
  renewal: "续费码",
  test: "测试激活码",
};

const PRODUCT_LABELS = {
  lite: "轻量版",
  quality: "质量版",
  universal: "通用版",
};

const STATUS_LABELS = {
  none: "未开通",
  active: "生效中",
  frozen: "已冻结",
  expired: "已过期",
  unused: "未使用",
  used: "已使用",
  invalidated: "已手动失效",
  inactive: "已解绑",
};

const state = {
  users: [],
  codes: [],
  devices: [],
  latestCreated: [],
  authenticated: false,
  adminEmail: "",
  editingCode: "",
};

function $(id) {
  return document.getElementById(id);
}

function text(value) {
  return String(value == null ? "" : value);
}

function escapeHtml(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return date.toLocaleString();
}

function productLabel(value) {
  return PRODUCT_LABELS[text(value).toLowerCase()] || text(value || "通用版");
}

function typeLabel(value) {
  return TYPE_LABELS[text(value).toLowerCase()] || text(value || "-");
}

function statusLabel(value) {
  return STATUS_LABELS[text(value).toLowerCase()] || text(value || "-");
}

function tagClass(status) {
  const value = text(status).toLowerCase();
  if (value === "active" || value === "unused") return `tag ${value}`;
  if (value === "frozen" || value === "error" || value === "invalidated" || value === "inactive") return "tag frozen";
  if (value === "expired") return "tag expired";
  return "tag";
}

function formatDuration(item) {
  const minutes = Math.max(0, Number(item?.duration_minutes || 0));
  const days = Math.max(0, Number(item?.duration_days || 0));
  if (minutes > 0) return `${minutes} 分钟`;
  if (days > 0) return `${days} 天`;
  return "-";
}

function showMessage(kind, message) {
  const errorEl = $("global-error");
  const successEl = $("global-success");
  errorEl.classList.add("hidden");
  successEl.classList.add("hidden");
  if (!message) return;
  const target = kind === "success" ? successEl : errorEl;
  target.textContent = message;
  target.classList.remove("hidden");
}

function clearMessages() {
  showMessage("error", "");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const errorMessage = payload && typeof payload === "object" ? payload.error || payload.message : response.statusText;
    throw new Error(text(errorMessage || "请求失败"));
  }
  return payload || {};
}

function renderEmptyRow(colspan, message) {
  return `<tr><td colspan="${colspan}" class="empty">${escapeHtml(message)}</td></tr>`;
}

function selectedEditCode() {
  return state.codes.find((item) => text(item.code).toUpperCase() === state.editingCode) || null;
}

function setCodeEditModalOpen(open) {
  $("code-edit-modal").classList.toggle("hidden", !open);
  document.body.classList.toggle("modal-open", open);
}

function renderUsers() {
  const tbody = $("users-table");
  const keyword = text($("user-keyword").value).trim().toLowerCase();
  const items = state.users.filter((item) => {
    if (!keyword) return true;
    return [item.email, statusLabel(item.license_status), typeLabel(item.license_type), productLabel(item.product_edition)]
      .some((value) => text(value).toLowerCase().includes(keyword));
  });
  if (!items.length) {
    tbody.innerHTML = renderEmptyRow(8, state.authenticated ? "暂无用户数据" : "请先登录管理员账号");
    return;
  }
  tbody.innerHTML = items
    .map((item) => {
      const frozen = text(item.license_status) === "frozen";
      return `
        <tr>
          <td><strong>${escapeHtml(item.email)}</strong></td>
          <td><span class="${tagClass(item.license_status)}">${escapeHtml(statusLabel(item.license_status || "none"))}</span></td>
          <td>${escapeHtml(typeLabel(item.license_type || "-"))}</td>
          <td>${escapeHtml(productLabel(item.product_edition || "universal"))}</td>
          <td>${escapeHtml(formatTime(item.expire_at))}</td>
          <td>${escapeHtml(item.active_device_count ?? 0)}</td>
          <td>${escapeHtml(formatTime(item.last_login_at))}</td>
          <td>
            <div class="cell-actions">
              <button class="button text-action" data-action="freeze-toggle" data-email="${escapeHtml(item.email)}" data-freeze="${String(!frozen)}">
                ${frozen ? "恢复授权" : "冻结授权"}
              </button>
              <button class="button text-action" data-action="extend" data-email="${escapeHtml(item.email)}" data-days="30">延长 30 天</button>
              <button class="button text-action" data-action="extend" data-email="${escapeHtml(item.email)}" data-days="365">延长 365 天</button>
              <button class="button text-action danger-link" data-action="delete-user" data-email="${escapeHtml(item.email)}">删除用户</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderCodes() {
  const tbody = $("codes-table");
  const keyword = text($("code-keyword")?.value).trim().toLowerCase();
  const items = state.codes.filter((item) => {
    if (!keyword) return true;
    return [item.code, typeLabel(item.type), statusLabel(item.status), productLabel(item.product_edition), item.used_by_email, item.used_by_user_id]
      .some((value) => text(value).toLowerCase().includes(keyword));
  });
  if (!items.length) {
    tbody.innerHTML = renderEmptyRow(9, state.authenticated ? "暂无激活码数据" : "请先登录管理员账号");
    return;
  }
  tbody.innerHTML = items
    .map((item) => {
      const code = text(item.code).toUpperCase();
      const status = text(item.status).toLowerCase();
      const isUsed = status === "used";
      const isInvalidated = status === "invalidated";
      const canEdit = !isUsed;
      const isSelected = state.editingCode === code;
      return `
        <tr class="${isSelected ? "row-selected" : ""}">
          <td><code>${escapeHtml(code)}</code></td>
          <td>${escapeHtml(typeLabel(item.type))}</td>
          <td>${escapeHtml(productLabel(item.product_edition || "universal"))}</td>
          <td>${escapeHtml(formatDuration(item))}</td>
          <td><span class="${tagClass(status)}">${escapeHtml(statusLabel(status))}</span></td>
          <td>${escapeHtml(item.used_by_email || item.used_by_user_id || "-")}</td>
          <td>${escapeHtml(formatTime(item.used_at))}</td>
          <td>${escapeHtml(formatTime(item.created_at))}</td>
          <td>
            <div class="cell-actions">
              <button class="button text-action" data-action="edit-code" data-code="${escapeHtml(code)}" ${canEdit ? "" : "disabled"}>编辑</button>
              <button class="button text-action" data-action="invalidate-code" data-code="${escapeHtml(code)}" ${canEdit && !isInvalidated ? "" : "disabled"}>
                ${isInvalidated ? "已失效" : "手动失效"}
              </button>
              <button class="button text-action danger-link" data-action="delete-code" data-code="${escapeHtml(code)}" ${canEdit ? "" : "disabled"}>删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderDevices() {
  const tbody = $("devices-table");
  const keyword = text($("device-keyword").value).trim().toLowerCase();
  const items = state.devices.filter((item) => {
    if (!keyword) return true;
    return [item.email, item.device_id, item.device_name, item.platform].some((value) => text(value).toLowerCase().includes(keyword));
  });
  if (!items.length) {
    tbody.innerHTML = renderEmptyRow(9, state.authenticated ? "暂无设备数据" : "请先登录管理员账号");
    return;
  }
  tbody.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td><strong>${escapeHtml(item.email)}</strong></td>
          <td><code>${escapeHtml(item.device_id)}</code></td>
          <td>${escapeHtml(item.device_name || "-")}</td>
          <td>${escapeHtml(item.platform || "-")}</td>
          <td><span class="${tagClass(item.active ? "active" : "inactive")}">${escapeHtml(statusLabel(item.active ? "active" : "inactive"))}</span></td>
          <td><span class="${tagClass(item.license_status)}">${escapeHtml(statusLabel(item.license_status || "none"))}</span></td>
          <td>${escapeHtml(productLabel(item.product_edition || "universal"))}</td>
          <td>${escapeHtml(formatTime(item.last_seen_at))}</td>
          <td>
            <button class="button text-action danger-link" data-action="unbind-row" data-email="${escapeHtml(item.email)}" data-device-id="${escapeHtml(item.device_id)}">解绑</button>
          </td>
        </tr>
      `
    )
    .join("");
}

function renderLatestCreated() {
  const box = $("latest-created");
  if (!state.latestCreated.length) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  box.classList.remove("hidden");
  box.textContent = state.latestCreated
    .map((item) => `${item.code}  [${productLabel(item.product_edition)} / ${typeLabel(item.type)} / ${formatDuration(item)}]`)
    .join("\n");
}

function updateCreateDurationUI() {
  const type = text($("create-type").value).trim().toLowerCase();
  const daysInput = $("create-days");
  const hint = $("create-duration-hint");
  if (type === "test") {
    daysInput.value = "0";
    daysInput.disabled = true;
    hint.textContent = "测试激活码固定为“兑换后 3 分钟到期”，适合快速验证到期、续费和恢复流程。";
  } else {
    if (Number(daysInput.value || 0) <= 0) {
      daysInput.value = "30";
    }
    daysInput.disabled = false;
    hint.textContent = "普通激活码按“天”计算有效时长；若要临时验证快速到期，请选择“测试激活码（3 分钟）”。";
  }
}

function updateEditDurationUI() {
  const type = text($("edit-type").value).trim().toLowerCase();
  const daysInput = $("edit-days");
  const minutesInput = $("edit-minutes");
  const hint = $("edit-duration-hint");
  if (!state.editingCode) {
    hint.textContent = "请先在上方列表中选择一条未使用的激活码。";
    return;
  }
  if (type === "test") {
    if (Number(minutesInput.value || 0) <= 0) {
      minutesInput.value = "3";
    }
    hint.textContent = "测试激活码建议按“分钟”设置，默认 3 分钟；若分钟大于 0，系统会优先按分钟计算。";
  } else {
    hint.textContent = "普通激活码通常按“天”设置；如果分钟大于 0，系统会优先按分钟计算。";
    if (Number(daysInput.value || 0) <= 0 && Number(minutesInput.value || 0) <= 0) {
      daysInput.value = "30";
    }
  }
}

function fillCodeEditor(item) {
  state.editingCode = text(item?.code).toUpperCase();
  $("edit-code").value = state.editingCode;
  $("edit-type").value = text(item?.type || "standard").toLowerCase();
  $("edit-product-edition").value = text(item?.product_edition || "universal").toLowerCase();
  $("edit-days").value = String(Math.max(0, Number(item?.duration_days || 0)));
  $("edit-minutes").value = String(Math.max(0, Number(item?.duration_minutes || 0)));
  updateEditDurationUI();
  setCodeEditModalOpen(true);
  renderCodes();
}

function clearCodeEditor() {
  state.editingCode = "";
  $("edit-code").value = "";
  $("edit-type").value = "standard";
  $("edit-product-edition").value = "universal";
  $("edit-days").value = "30";
  $("edit-minutes").value = "0";
  updateEditDurationUI();
  setCodeEditModalOpen(false);
  renderCodes();
}

function renderAll() {
  $("login-form").classList.toggle("hidden", state.authenticated);
  $("session-bar").classList.toggle("hidden", !state.authenticated);
  $("admin-app").classList.toggle("hidden", !state.authenticated);
  $("session-email").textContent = state.adminEmail ? `已登录：${state.adminEmail}` : "";
  renderUsers();
  renderCodes();
  renderDevices();
  renderLatestCreated();
  updateCreateDurationUI();
  updateEditDurationUI();
}

async function loadUsers() {
  state.users = (await requestJson("/api/admin/users")).items || [];
}

async function loadCodes() {
  state.codes = (await requestJson("/api/admin/activation-codes")).items || [];
  if (state.editingCode && !selectedEditCode()) {
    clearCodeEditor();
  }
}

async function loadDevices() {
  state.devices = (await requestJson("/api/admin/devices")).items || [];
}

async function refreshAll() {
  if (!state.authenticated) {
    showMessage("error", "请先登录管理员账号。");
    renderAll();
    return;
  }
  clearMessages();
  await Promise.all([loadUsers(), loadCodes(), loadDevices()]);
  renderAll();
}

async function handleCreateCodes() {
  if (!state.authenticated) {
    showMessage("error", "请先登录管理员账号。");
    return;
  }
  const type = text($("create-type").value).trim().toLowerCase() || "standard";
  const payload = {
    count: Math.max(1, Number($("create-count").value || 1)),
    type,
    product_edition: text($("create-product-edition").value).trim() || "lite",
  };
  if (type === "test") {
    payload.duration_minutes = 3;
  } else {
    payload.duration_days = Math.max(1, Number($("create-days").value || 30));
  }
  const button = $("create-codes");
  button.disabled = true;
  try {
    clearMessages();
    const data = await requestJson("/api/admin/activation-codes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.latestCreated = Array.isArray(data.items) ? data.items : [];
    showMessage("success", `已生成 ${state.latestCreated.length} 个激活码。`);
    await loadCodes();
    renderAll();
  } finally {
    button.disabled = false;
  }
}

async function handleFreezeToggle(email, freeze) {
  await requestJson("/api/admin/licenses/freeze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, freeze }),
  });
  showMessage("success", freeze ? "已冻结该账号授权。" : "已恢复该账号授权。");
  await refreshAll();
}

async function handleExtend(email, days) {
  const data = await requestJson("/api/admin/licenses/extend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, days }),
  });
  const suffix = data && data.expire_at ? `，新到期时间：${formatTime(data.expire_at)}` : "";
  showMessage("success", `已延长 ${days} 天${suffix}。`);
  await refreshAll();
}

async function handleDeleteUser(email) {
  const ok = window.confirm(`确定删除用户 ${email} 吗？这会同时删除该用户的授权、设备绑定和验证码记录，且不可恢复。`);
  if (!ok) return;
  await requestJson(`/api/admin/users/${encodeURIComponent(email)}`, {
    method: "DELETE",
  });
  showMessage("success", `用户 ${email} 已删除。`);
  await refreshAll();
}

async function handleUnbind(email, deviceId) {
  await requestJson("/api/admin/devices/unbind", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, device_id: deviceId }),
  });
  showMessage("success", "设备解绑完成。");
  await refreshAll();
}

async function handleSaveCodeEdit() {
  if (!state.editingCode) {
    showMessage("error", "请先从激活码列表选择一条未使用的激活码。");
    return;
  }
  const type = text($("edit-type").value).trim().toLowerCase() || "standard";
  const payload = {
    type,
    product_edition: text($("edit-product-edition").value).trim() || "universal",
    duration_days: Math.max(0, Number($("edit-days").value || 0)),
    duration_minutes: Math.max(0, Number($("edit-minutes").value || 0)),
  };
  if (type === "test" && payload.duration_minutes <= 0) {
    payload.duration_minutes = 3;
  }
  if (payload.duration_minutes <= 0 && payload.duration_days <= 0) {
    payload.duration_days = 30;
  }
  const button = $("save-code-edit");
  button.disabled = true;
  try {
    const data = await requestJson(`/api/admin/activation-codes/${encodeURIComponent(state.editingCode)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    showMessage("success", `已更新激活码 ${data?.item?.code || state.editingCode}。`);
    await loadCodes();
    clearCodeEditor();
    renderAll();
  } finally {
    button.disabled = false;
  }
}

async function handleInvalidateCode(code) {
  await requestJson(`/api/admin/activation-codes/${encodeURIComponent(code)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ invalidate: true }),
  });
  showMessage("success", `激活码 ${code} 已手动失效。`);
  await loadCodes();
  if (state.editingCode === code) clearCodeEditor();
  renderAll();
}

async function handleDeleteCode(code) {
  const ok = window.confirm(`确定删除激活码 ${code} 吗？删除后不可恢复。`);
  if (!ok) return;
  await requestJson(`/api/admin/activation-codes/${encodeURIComponent(code)}`, {
    method: "DELETE",
  });
  if (state.editingCode === code) {
    clearCodeEditor();
  }
  showMessage("success", `激活码 ${code} 已删除。`);
  await loadCodes();
  renderAll();
}

function bindTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      document.querySelectorAll(".tab-button").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${tab}`));
    });
  });
}

async function fetchAdminSession() {
  try {
    const data = await requestJson("/api/admin/me");
    state.authenticated = true;
    state.adminEmail = text(data?.user?.email).trim();
  } catch {
    state.authenticated = false;
    state.adminEmail = "";
  }
}

function bindActions() {
  $("create-type").addEventListener("change", updateCreateDurationUI);
  $("edit-type").addEventListener("change", updateEditDurationUI);
  $("close-code-edit").addEventListener("click", clearCodeEditor);

  $("login-admin").addEventListener("click", async () => {
    const email = text($("admin-email").value).trim().toLowerCase();
    const password = text($("admin-password").value);
    if (!email || !password) {
      showMessage("error", "请输入管理员邮箱和登录密码。");
      return;
    }
    const button = $("login-admin");
    button.disabled = true;
    try {
      clearMessages();
      const data = await requestJson("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      state.authenticated = true;
      state.adminEmail = text(data?.user?.email).trim();
      $("admin-password").value = "";
      showMessage("success", "管理员登录成功。");
      await refreshAll();
    } catch (error) {
      state.authenticated = false;
      state.adminEmail = "";
      showMessage("error", error.message || "管理员登录失败");
      renderAll();
    } finally {
      button.disabled = false;
    }
  });

  $("logout-admin").addEventListener("click", async () => {
    try {
      await requestJson("/api/admin/logout", { method: "POST" });
    } catch {
      // ignore logout errors
    }
    state.authenticated = false;
    state.adminEmail = "";
    state.users = [];
    state.codes = [];
    state.devices = [];
    state.latestCreated = [];
    clearCodeEditor();
    clearMessages();
    renderAll();
  });

  $("refresh-all").addEventListener("click", async () => {
    try {
      await refreshAll();
    } catch (error) {
      showMessage("error", error.message || "刷新失败");
    }
  });

  $("create-codes").addEventListener("click", async () => {
    try {
      await handleCreateCodes();
    } catch (error) {
      showMessage("error", error.message || "生成激活码失败");
    }
  });

  $("save-code-edit").addEventListener("click", async () => {
    try {
      await handleSaveCodeEdit();
    } catch (error) {
      showMessage("error", error.message || "保存激活码失败");
    }
  });

  $("clear-code-edit").addEventListener("click", () => {
    clearCodeEditor();
  });

  $("manual-unbind").addEventListener("click", async () => {
    const email = text($("unbind-email").value).trim().toLowerCase();
    const deviceId = text($("unbind-device-id").value).trim();
    if (!email || !deviceId) {
      showMessage("error", "请输入账号邮箱和设备 ID。");
      return;
    }
    try {
      await handleUnbind(email, deviceId);
      $("unbind-device-id").value = "";
    } catch (error) {
      showMessage("error", error.message || "解绑设备失败");
    }
  });

  $("user-keyword").addEventListener("input", renderUsers);
  $("code-keyword").addEventListener("input", renderCodes);
  $("device-keyword").addEventListener("input", renderDevices);

  document.body.addEventListener("click", async (event) => {
    if (event.target instanceof HTMLElement && event.target.dataset.closeCodeEdit === "true") {
      clearCodeEditor();
      return;
    }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const email = text(button.dataset.email).trim().toLowerCase();
    const deviceId = text(button.dataset.deviceId).trim();
    const code = text(button.dataset.code).trim().toUpperCase();
    try {
      button.disabled = true;
      if (action === "freeze-toggle") {
        const freeze = text(button.dataset.freeze) === "true";
        await handleFreezeToggle(email, freeze);
      } else if (action === "extend") {
        await handleExtend(email, Number(button.dataset.days || 30));
      } else if (action === "delete-user") {
        await handleDeleteUser(email);
      } else if (action === "unbind-row") {
        await handleUnbind(email, deviceId);
      } else if (action === "edit-code") {
        const item = state.codes.find((row) => text(row.code).toUpperCase() === code);
        if (item) {
          fillCodeEditor(item);
          clearMessages();
        }
      } else if (action === "invalidate-code") {
        await handleInvalidateCode(code);
      } else if (action === "delete-code") {
        await handleDeleteCode(code);
      }
    } catch (error) {
      showMessage("error", error.message || "操作失败");
    } finally {
      button.disabled = false;
    }
  });
}

async function bootstrap() {
  bindTabs();
  bindActions();
  updateCreateDurationUI();
  updateEditDurationUI();
  await fetchAdminSession();
  renderAll();
  if (state.authenticated) {
    try {
      await refreshAll();
    } catch (error) {
      showMessage("error", error.message || "连接管理后台失败");
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  void bootstrap();
});
