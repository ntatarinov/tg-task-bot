"use strict";

// Everything from the server goes in through textContent: chat titles and messages are
// written by Telegram users, so nothing here may build HTML from strings.
const $ = (id) => document.getElementById(id);
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6].map((day) => t(`weekday.${day}`));
const KINDS = {
  decision: t("kind.decision"), proposal: t("kind.proposal"), task: t("kind.task"),
  question: t("kind.question"), blocker: t("kind.blocker"), next_action: t("kind.next_action"),
};
const SAMPLE = t("sandbox.sample");

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

function requestHeaders(hasBody) {
  const headers = { "X-Panel-Language": LANG };
  if (hasBody) headers["Content-Type"] = "application/json";
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: requestHeaders(options.body),
  });
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error(t("api.sign_in"));
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : t("api.failed"));
    error.detail = data.detail;
    throw error;
  }
  return data;
}

function showLogin() {
  $("app").hidden = true;
  $("login").hidden = false;
  $("password").focus();
}

async function showApp() {
  $("login").hidden = true;
  $("app").hidden = false;
  await openTab("summary");
}

async function openTab(name) {
  for (const button of document.querySelectorAll("#tabs button")) {
    if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const tab of ["summary", "settings", "sandbox", "move"]) $(`tab-${tab}`).hidden = tab !== name;
  if (name === "summary") await loadSummary();
  if (name === "settings") await Promise.all([loadSettings(), loadStacks(), loadChats()]);
  if (name === "sandbox") await loadSandbox();
  if (name === "move") await loadMove();
}

function showRestart(text) {
  $("restart-text").textContent = text;
  $("restart").hidden = false;
  $("restart-bar").hidden = false;
}

function fact(label, value) {
  return el("div", { class: "fact" }, el("span", { textContent: label }), el("strong", { textContent: value }));
}

// --- Summary ----------------------------------------------------------------

function renderSetup(steps) {
  const box = $("setup");
  box.hidden = !steps.length;
  box.replaceChildren(
    el("h2", { textContent: t("setup.title") }),
    el("ol", {}, ...steps.map((step) => el("li", { textContent: step }))),
  );
}

function detailLine(item) {
  const parts = [];
  if (item.owner !== undefined) parts.push(item.owner || t("item.no_owner"));
  if (item.deadline) parts.push(t("item.deadline", { value: item.deadline }));
  return parts.length ? el("small", { textContent: parts.join(" · ") }) : null;
}

function taskRow(task) {
  const done = el("button", { type: "button", class: "small", textContent: t("common.done") });
  done.addEventListener("click", async () => {
    await api(`/api/tasks/${task.id}/status`, { method: "POST", body: JSON.stringify({ status: "done" }) });
    await loadSummary();
  });
  return el("li", { class: "row" },
    el("div", {}, el("span", { textContent: task.text }), detailLine(task)),
    done,
  );
}

function board(title, rows, empty) {
  return el("section", { class: "column" },
    el("h3", { textContent: rows.length ? `${title} · ${rows.length}` : title }),
    rows.length ? el("ul", {}, ...rows) : el("p", { class: "hint", textContent: empty }),
  );
}

function plainRow(item) {
  return el("li", {}, el("span", { textContent: item.text }), detailLine(item));
}

async function loadSummary() {
  const chatId = $("summary-chat").value;
  const query = new URLSearchParams({ days: $("summary-days").value });
  if (chatId) query.set("chat_id", chatId);
  const data = await api(`/api/summary?${query}`);
  renderSetup(data.setup);

  const select = $("summary-chat");
  select.replaceChildren(
    ...data.chats.map((chat) => el("option", { value: String(chat.id), textContent: chat.title || t("common.untitled") })),
  );
  if (data.chat_id) select.value = String(data.chat_id);

  if (!data.chat_id) {
    $("summary-body").replaceChildren(el("p", { class: "hint", textContent: t("summary.empty") }));
    $("digest-meta").textContent = "";
    $("digest-text").textContent = "";
    return;
  }
  $("summary-body").replaceChildren(
    board(t("board.decisions"), data.decisions.map(plainRow), t("board.no_decisions")),
    board(t("board.tasks"), data.tasks.map(taskRow), t("board.no_tasks")),
    board(t("board.blockers"), data.blockers.map(plainRow), t("board.no_blockers")),
    board(t("board.questions"), data.questions.map(plainRow), t("board.no_questions")),
  );
  if (data.pending) {
    $("summary-body").append(el("p", { class: "hint wide", textContent: t("summary.pending", { count: data.pending }) }));
  }
  $("digest-meta").textContent = data.digest
    ? t("summary.last_report", { when: new Date(data.digest.sent_at).toLocaleString(LANG === "ru" ? "ru-RU" : "en-GB") })
    : t("summary.no_reports");
  $("digest-text").textContent = data.digest ? data.digest.text : "";
}

async function buildDigest() {
  const button = $("digest-build");
  button.disabled = true;
  $("digest-meta").textContent = t("digest.building");
  try {
    const data = await api("/api/digest-preview", {
      method: "POST",
      body: JSON.stringify({ chat_id: Number($("summary-chat").value), days: Number($("summary-days").value) }),
    });
    $("digest-text").textContent = data.text;
    $("digest-meta").textContent = t("digest.preview");
  } catch (error) {
    $("digest-meta").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

// --- Step 1: connection -----------------------------------------------------

let settingFields = [];

function renderControl(field) {
  const id = `f-${field.key}`;
  if (field.kind === "bool") {
    return el("input", { id, type: "checkbox", checked: field.value === "true", disabled: field.locked });
  }
  if (field.kind === "select") {
    const select = el("select", { id, disabled: field.locked });
    for (const option of field.options) {
      select.append(el("option", { value: option, textContent: option || t("common.not_set"), selected: option === field.value }));
    }
    return select;
  }
  return el("input", {
    id,
    type: field.kind === "secret" ? "password" : "text",
    autocomplete: "off",
    disabled: field.locked,
    value: field.kind === "secret" ? "" : field.value,
    placeholder: field.kind === "secret" ? (field.is_set ? t("field.secret_set") : t("field.secret_unset")) : "",
  });
}

function renderField(field) {
  const id = `f-${field.key}`;
  const note = field.locked ? t("field.locked") : field.help;
  const error = el("p", { id: `e-${field.key}`, class: "error", role: "alert" });
  if (field.kind === "bool") {
    const parts = [
      el("div", { class: "check-row" }, renderControl(field), el("label", { htmlFor: id, textContent: field.label })),
      note ? el("small", { textContent: note }) : null,
    ];
    if (field.key === "LLM_EXTERNAL_ALLOWED") {
      parts.push(
        el("div", { class: "confirm" },
          el("input", { id: "confirm-external", type: "checkbox" }),
          el("label", { htmlFor: "confirm-external", textContent: t("field.confirm_owner") }),
        ),
      );
    }
    return el("div", { class: "field" }, ...parts, error);
  }
  return el("div", { class: "field" },
    el("label", { htmlFor: id, textContent: field.label }),
    renderControl(field),
    note ? el("small", { textContent: note }) : null,
    error,
  );
}

async function loadSettings() {
  const { fields } = await api("/api/settings");
  settingFields = fields;
  $("fields-basic").replaceChildren(
    el("fieldset", {}, ...fields.filter((f) => f.basic).map(renderField)),
  );
  const groups = new Map();
  for (const field of fields.filter((f) => !f.basic)) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  }
  const nodes = [];
  for (const [group, items] of groups) {
    nodes.push(el("fieldset", {}, el("legend", { textContent: group }), ...items.map(renderField)));
  }
  $("fields-advanced").replaceChildren(...nodes);
  $("save-status").textContent = "";
}

function collectValues() {
  const values = {};
  for (const field of settingFields) {
    if (field.locked) continue;
    const control = $(`f-${field.key}`);
    values[field.key] = control.type === "checkbox" ? String(control.checked) : control.value;
  }
  return values;
}

async function saveSettings(event) {
  event.preventDefault();
  for (const field of settingFields) $(`e-${field.key}`).textContent = "";
  const status = $("save-status");
  status.textContent = t("common.saving");
  try {
    const result = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ values: collectValues(), confirm_external: $("confirm-external").checked }),
    });
    if (result.restart_needed) showRestart(t("settings.restart"));
    await loadSettings();
    status.textContent = result.changed.length ? t("settings.saved_count", { count: result.changed.length }) : t("settings.no_changes");
  } catch (error) {
    const errors = error.detail && error.detail.errors;
    if (errors) {
      for (const [key, message] of Object.entries(errors)) {
        const target = $(`e-${key}`);
        if (target) target.textContent = message;
      }
      status.textContent = t("common.fix_fields");
    } else {
      status.textContent = error.message;
    }
  }
}

// --- Step 1: model ----------------------------------------------------------

function stackCard(stack, installed) {
  const place = stack.external ? t("stack.external") : t("stack.local");
  const missing = !stack.external && installed.length && !installed.includes(stack.model);
  const confirmBox = el("input", { id: `confirm-${stack.id}`, type: "checkbox" });
  const status = el("span", { role: "status" });
  const apply = stack.active ? null : el("button", { type: "button", textContent: t("stack.select") });
  if (apply) {
    apply.addEventListener("click", async () => {
      status.textContent = t("stack.applying");
      try {
        await api(`/api/stacks/${stack.id}/apply`, {
          method: "POST",
          body: JSON.stringify({ confirm_external: confirmBox.checked }),
        });
        showRestart(t("stack.restart"));
        await loadStacks();
      } catch (error) {
        status.textContent = error.message;
      }
    });
  }
  const remove = stack.builtin ? null : el("button", { type: "button", textContent: t("common.delete") });
  if (remove) {
    remove.addEventListener("click", async () => {
      await api(`/api/stacks/${stack.id}`, { method: "DELETE" });
      await loadStacks();
    });
  }
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: stack.name }),
      el("span", { class: `pill ${stack.active ? "on" : "off"}`, textContent: stack.active ? t("stack.active") : place }),
      apply,
      remove,
    ),
    missing ? el("p", { class: "error", textContent: t("stack.missing") }) : null,
    stack.external && !stack.active
      ? el("div", { class: "confirm" }, confirmBox,
          el("label", { htmlFor: confirmBox.id, textContent: t("field.confirm_owner") }))
      : null,
    status,
  );
}

async function loadStacks() {
  const [data, { models }] = await Promise.all([api("/api/stacks"), api("/api/models")]);
  $("stacks").replaceChildren(...data.stacks.map((stack) => stackCard(stack, models)));
  $("models-installed").textContent = models.length
    ? t("models.installed", { models: models.join(", ") })
    : t("models.none");
}

async function addStack(event) {
  event.preventDefault();
  const fields = ["name", "base_url", "model", "api_key", "reasoning_effort", "max_output_tokens", "timeout_seconds"];
  for (const key of fields) $(`se-${key}`).textContent = "";
  const body = Object.fromEntries(fields.map((key) => [key, $(`s-${key}`).value]));
  body.max_output_tokens = Number(body.max_output_tokens);
  body.timeout_seconds = Number(body.timeout_seconds);
  try {
    await api("/api/stacks", { method: "POST", body: JSON.stringify(body) });
    $("s-api_key").value = "";
    $("stack-status").textContent = t("stack.added");
    await loadStacks();
  } catch (error) {
    const errors = error.detail && error.detail.errors;
    if (errors) {
      for (const [key, message] of Object.entries(errors)) $(`se-${key}`).textContent = message;
      $("stack-status").textContent = t("common.fix_fields");
    } else {
      $("stack-status").textContent = error.message;
    }
  }
}

async function pullModel(event) {
  event.preventDefault();
  const status = $("pull-status");
  try {
    await api("/api/models/pull", { method: "POST", body: JSON.stringify({ model: $("pull-model").value }) });
  } catch (error) {
    status.textContent = error.message;
    return;
  }
  const timer = setInterval(async () => {
    const state = await api("/api/models/pull");
    status.textContent = state.status === "running" ? t("pull.running", { model: state.model, percent: state.percent }) : state.error || t("pull.done");
    if (state.status !== "running") {
      clearInterval(timer);
      await loadStacks();
    }
  }, 2000);
}

// --- Step 1: chats ----------------------------------------------------------

async function setAccess(telegramChatId, allowed) {
  await api("/api/chats/access", {
    method: "POST",
    body: JSON.stringify({ telegram_chat_id: telegramChatId, allowed }),
  });
  showRestart(t("chats.restart"));
  await loadChats();
}

function waitingCard(chat) {
  const allow = el("button", { type: "button", class: "primary", textContent: t("chat.allow") });
  allow.addEventListener("click", () => setAccess(chat.telegram_chat_id, true));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || t("common.untitled") }),
      el("span", { class: "pill off", textContent: t("chat.waiting") }),
      allow,
    ),
  );
}

function allowedEmptyCard(chat) {
  const forbid = el("button", { type: "button", textContent: t("chat.block") });
  forbid.addEventListener("click", () => setAccess(chat.telegram_chat_id, false));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: String(chat.telegram_chat_id) }),
      el("span", { class: "pill on", textContent: t("chat.allowed_no_messages") }),
      forbid,
    ),
  );
}

function chatCard(chat) {
  const prefix = `c${chat.id}`;
  const tz = el("input", { id: `${prefix}-tz`, value: chat.timezone });
  const time = el("input", { id: `${prefix}-time`, value: chat.digest_time });
  const daily = el("input", { id: `${prefix}-daily`, type: "checkbox", checked: chat.digest_enabled });
  const weekly = el("input", { id: `${prefix}-weekly`, type: "checkbox", checked: chat.weekly_digest_enabled });
  const day = el("select", { id: `${prefix}-day` },
    ...WEEKDAYS.map((name, index) => el("option", { value: String(index), textContent: name, selected: index === chat.weekly_digest_day })),
  );
  const target = el("input", { id: `${prefix}-target`, value: chat.digest_chat_id ?? "", placeholder: t("chat.this_chat") });
  const language = el("select", { id: `${prefix}-language` },
    el("option", { value: "", textContent: t("chat.language_default"), selected: !chat.language }),
    ...Object.entries(LANGUAGE_NAMES).map(([code, name]) => el("option", { value: code, textContent: name, selected: chat.language === code })),
  );
  const status = el("span", { role: "status" });
  const save = el("button", { type: "button", class: "primary", textContent: t("common.save") });

  save.addEventListener("click", async () => {
    const raw = target.value.trim();
    const body = {
      timezone: tz.value.trim(),
      digest_time: time.value.trim(),
      digest_enabled: daily.checked,
      weekly_digest_enabled: weekly.checked,
      weekly_digest_day: Number(day.value),
      language: language.value,
    };
    if (raw) body.digest_chat_id = Number(raw);
    else body.clear_digest_chat = true;
    status.textContent = t("common.saving");
    try {
      await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify(body) });
      status.textContent = t("common.saved");
    } catch (error) {
      status.textContent = error.message;
    }
  });

  const access = el("button", {
    type: "button",
    class: chat.allowed ? "" : "primary",
    textContent: chat.allowed ? t("chat.block") : t("chat.allow"),
  });
  access.addEventListener("click", () => setAccess(chat.telegram_chat_id, !chat.allowed));

  // Deleting is permanent, so the first press only arms the button.
  const purge = el("button", { type: "button", textContent: t("chat.purge") });
  let armed = false;
  purge.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      purge.textContent = t("chat.purge_confirm");
      return;
    }
    try {
      const result = await api(`/api/chats/${chat.id}/purge`, { method: "POST", body: JSON.stringify({ confirm: true }) });
      status.textContent = t("chat.purged", { count: result.removed });
      purge.textContent = t("chat.purge");
      armed = false;
    } catch (error) {
      status.textContent = error.message;
    }
  });

  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || t("common.untitled") }),
      el("span", { class: `pill ${chat.allowed ? "on" : "off"}`, textContent: chat.allowed ? t("chat.allowed") : t("chat.not_allowed") }),
      access,
    ),
    el("details", {},
      el("summary", { textContent: t("chat.reports", { state: chat.digest_enabled ? t("chat.reports_daily", { time: chat.digest_time }) : t("chat.reports_off") }) }),
      el("div", { class: "stack" },
        el("div", { class: "grid2" },
          el("div", { class: "field" }, el("label", { htmlFor: time.id, textContent: t("chat.report_time") }), time),
          el("div", { class: "field" }, el("label", { htmlFor: tz.id, textContent: t("chat.timezone") }), tz),
          el("div", { class: "field" }, el("label", { htmlFor: day.id, textContent: t("chat.weekly_day") }), day),
          el("div", { class: "field" }, el("label", { htmlFor: target.id, textContent: t("chat.target") }), target),
          el("div", { class: "field" }, el("label", { htmlFor: language.id, textContent: t("chat.language") }), language),
        ),
        el("div", { class: "check-row" }, daily, el("label", { htmlFor: daily.id, textContent: t("chat.daily") })),
        el("div", { class: "check-row" }, weekly, el("label", { htmlFor: weekly.id, textContent: t("chat.weekly") })),
        el("div", { class: "chat-foot" }, save, purge, status),
      ),
    ),
  );
}

async function loadChats() {
  const data = await api("/api/chats");
  $("waiting").replaceChildren(...data.waiting.map(waitingCard));
  const cards = [...data.chats.map(chatCard), ...data.allowed_without_messages.map(allowedEmptyCard)];
  $("chats").replaceChildren(
    ...(cards.length || data.waiting.length ? cards : [el("p", { class: "hint", textContent: t("chats.none") })]),
  );
}

function presenceRow(chat) {
  const labels = { in: t("presence.in"), out: t("presence.out"), moved: t("presence.moved") };
  const marks = { in: "ok", out: "fail", moved: "warn" };
  const note = chat.state === "moved"
    ? t("presence.moved_note", { id: chat.moved_to })
    : chat.state === "in" && !chat.allowed ? t("presence.not_allowed_note") : "";
  const targetId = chat.state === "moved" ? chat.moved_to : chat.telegram_chat_id;
  const canAllow = (chat.state === "in" && !chat.allowed) || chat.state === "moved";
  const allow = canAllow ? el("button", { type: "button", class: "small", textContent: t("chat.allow") }) : null;
  if (allow) allow.addEventListener("click", () => setAccess(targetId, true));
  return el("li", {},
    el("span", { class: `mark ${marks[chat.state]}`, textContent: labels[chat.state] }),
    el("span", {}, `${chat.title || chat.telegram_chat_id}${chat.allowed ? t("presence.allowed_mark") : ""} `, allow),
    note ? el("small", { textContent: note }) : null,
  );
}

async function checkPresence() {
  const button = $("presence");
  const status = $("presence-status");
  button.disabled = true;
  status.textContent = t("presence.asking");
  try {
    const data = await api("/api/chats/presence", { method: "POST" });
    $("presence-results").replaceChildren(...data.chats.map(presenceRow));
    status.textContent = data.chats.length
      ? t("presence.known", { bot: data.bot })
      : t("presence.none", { bot: data.bot });
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

// --- Step 2: checks ---------------------------------------------------------

async function runChecks() {
  const button = $("check");
  button.disabled = true;
  button.textContent = t("check.running");
  const list = $("check-results");
  try {
    const data = await api("/api/check", { method: "POST" });
    const labels = { ok: t("check.ok"), warn: t("check.warn"), fail: t("check.fail") };
    list.replaceChildren(
      ...data.items.map((item) =>
        el("li", {},
          el("span", { class: `mark ${item.state}`, textContent: labels[item.state] }),
          el("span", { textContent: item.text }),
          item.fix ? el("small", { textContent: item.fix }) : null,
        ),
      ),
    );
  } catch (error) {
    list.replaceChildren(el("li", { class: "error", textContent: error.message }));
  } finally {
    button.disabled = false;
    button.textContent = t("check.button");
  }
}

let sandboxStacks = [];

async function loadSandbox() {
  const data = await api("/api/stacks");
  sandboxStacks = [data.current, ...data.stacks];
  const select = $("sandbox-stack");
  const chosen = select.value;
  select.replaceChildren(
    ...sandboxStacks.map((stack) => el("option", { value: stack.id, textContent: `${stack.name} (${stack.model})` })),
  );
  if (chosen) select.value = chosen;
  if (!$("sandbox-text").value) $("sandbox-text").value = SAMPLE;
  updateSandboxConsent();
}

function updateSandboxConsent() {
  const stack = sandboxStacks.find((s) => s.id === $("sandbox-stack").value);
  $("sandbox-confirm").hidden = !(stack && stack.external);
}

function resultCard(data) {
  const topics = data.topics.map((topic) =>
    el("div", {},
      el("strong", { textContent: topic.project ? `${topic.title} · ${topic.project}` : topic.title }),
      el("ul", {}, ...topic.items.map((item) => {
        const details = [item.owner && t("sandbox.owner", { value: item.owner }), item.deadline && t("sandbox.deadline", { value: item.deadline })].filter(Boolean);
        return el("li", { class: item.status === "open" ? "" : "muted-item" },
          el("span", { class: "kind", textContent: `${KINDS[item.kind] || item.kind}: ` }),
          `${item.text}${details.length ? ` (${details.join(", ")})` : ""}`,
        );
      })),
    ),
  );
  const notes = [];
  if (data.rejected) notes.push(t("sandbox.rejected", { count: data.rejected }));
  if (data.failed) notes.push(t("sandbox.failed", { count: data.failed }));
  return el("article", { class: "result" },
    el("div", { class: "result-head" },
      document.createTextNode(data.stack),
      el("span", { textContent: t("sandbox.seconds", { model: data.model, seconds: data.seconds }) }),
    ),
    notes.length ? el("p", { class: "error", textContent: notes.join("; ") }) : null,
    ...(topics.length ? topics : [el("p", { class: "hint", textContent: t("sandbox.no_topics") })]),
    el("details", {}, el("summary", { textContent: t("sandbox.report_preview") }), el("pre", { class: "digest", textContent: data.digest })),
  );
}

async function runSandbox() {
  const button = $("sandbox-run");
  const status = $("sandbox-status");
  button.disabled = true;
  status.textContent = t("sandbox.running");
  try {
    const data = await api("/api/sandbox", {
      method: "POST",
      body: JSON.stringify({
        stack_id: $("sandbox-stack").value,
        text: $("sandbox-text").value,
        confirm_external: $("sandbox-confirm-box").checked,
      }),
    });
    $("sandbox-results").prepend(resultCard(data));
    status.textContent = t("sandbox.done");
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

// --- Step 3: moving ---------------------------------------------------------

async function loadMove() {
  const data = await api("/api/migration");
  $("move-summary").replaceChildren(
    fact(t("move.messages"), String(data.messages)),
    fact(t("move.chats"), String(data.chats)),
    fact(t("move.db_size"), data.database_included ? t("move.mb", { size: data.database_mb }) : t("move.not_in_archive")),
  );
  $("move-warning").hidden = data.database_included;
}

async function exportBundle(event) {
  event.preventDefault();
  const status = $("export-status");
  status.textContent = t("move.packing");
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("export-password").value }),
  });
  $("export-password").value = "";
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    status.textContent = typeof data.detail === "string" ? data.detail : t("move.failed");
    return;
  }
  const name = (response.headers.get("content-disposition") || "").match(/filename="?([^";]+)/);
  const link = el("a", { href: URL.createObjectURL(await response.blob()), download: name ? name[1] : "digest-bot.zip" });
  link.click();
  URL.revokeObjectURL(link.href);
  status.textContent = t("move.downloaded");
}

// --- Wiring -----------------------------------------------------------------

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("password").value }) });
    $("password").value = "";
    await showApp();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});

$("tabs").addEventListener("click", (event) => {
  const tab = event.target.dataset.tab;
  if (tab) openTab(tab).catch(() => {});
});

$("summary-chat").addEventListener("change", () => loadSummary().catch(() => {}));
$("summary-days").addEventListener("change", () => loadSummary().catch(() => {}));
$("digest-build").addEventListener("click", buildDigest);
$("settings-form").addEventListener("submit", saveSettings);
$("stack-form").addEventListener("submit", addStack);
$("pull-form").addEventListener("submit", pullModel);
$("check").addEventListener("click", runChecks);
$("presence").addEventListener("click", checkPresence);
$("sandbox-run").addEventListener("click", runSandbox);
$("sandbox-stack").addEventListener("change", updateSandboxConsent);
$("export-form").addEventListener("submit", exportBundle);

$("restart").addEventListener("click", async () => {
  await api("/api/restart", { method: "POST" });
  $("restart-text").textContent = t("restart.requested");
  $("restart").hidden = true;
});

api("/api/session")
  .then((state) => (state.authenticated ? showApp() : showLogin()))
  .catch(showLogin);
