"use strict";

// Everything from the server goes in through textContent: chat titles and messages are
// written by Telegram users, so nothing here may build HTML from strings.
const $ = (id) => document.getElementById(id);
const WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const KINDS = {
  decision: "Решение", proposal: "Предложение", task: "Задача",
  question: "Вопрос", blocker: "Блокер", next_action: "Следующий шаг",
};
const SAMPLE = `Аня: По Security Baseline предлагаю перейти на CIS Level 2.
Борис: Не уверен, это может сломать коннектор AD SCA.
Виктор: API AD SCA отдаёт 500 на /scan, это блокер для релиза.
Борис: Виктор, посмотри логи до пятницы.
Виктор: Да, возьму.
Дина: Решили: на фронте используем React Query.
Дина: Кто обновит сертификаты на стейдже?
Аня: Кто идёт на обед?`;

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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json" } : {},
  });
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Войдите в панель.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "Запрос не выполнен.");
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
    el("h2", { textContent: "Что сделать, чтобы бот заработал" }),
    el("ol", {}, ...steps.map((step) => el("li", { textContent: step }))),
  );
}

function detailLine(item) {
  const parts = [];
  if (item.owner !== undefined) parts.push(item.owner || "исполнитель не определён");
  if (item.deadline) parts.push(`срок ${item.deadline}`);
  return parts.length ? el("small", { textContent: parts.join(" · ") }) : null;
}

function taskRow(task) {
  const done = el("button", { type: "button", class: "small", textContent: "Готово" });
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
    ...data.chats.map((chat) => el("option", { value: String(chat.id), textContent: chat.title || "Без названия" })),
  );
  if (data.chat_id) select.value = String(data.chat_id);

  if (!data.chat_id) {
    $("summary-body").replaceChildren(el("p", { class: "hint", textContent: "Здесь появятся решения и задачи, когда бот начнёт работать в чате." }));
    $("digest-meta").textContent = "";
    $("digest-text").textContent = "";
    return;
  }
  $("summary-body").replaceChildren(
    board("Решения", data.decisions.map(plainRow), "Подтверждённых решений нет."),
    board("Задачи", data.tasks.map(taskRow), "Открытых задач нет."),
    board("Блокеры", data.blockers.map(plainRow), "Блокеров нет."),
    board("Открытые вопросы", data.questions.map(plainRow), "Вопросов нет."),
  );
  if (data.pending) {
    $("summary-body").append(el("p", { class: "hint wide", textContent: `Ещё не разобрано сообщений: ${data.pending}.` }));
  }
  $("digest-meta").textContent = data.digest
    ? `Последний отчёт: ${new Date(data.digest.sent_at).toLocaleString("ru-RU")}`
    : "Отчётов пока не было.";
  $("digest-text").textContent = data.digest ? data.digest.text : "";
}

async function buildDigest() {
  const button = $("digest-build");
  button.disabled = true;
  $("digest-meta").textContent = "Собираю отчёт…";
  try {
    const data = await api("/api/digest-preview", {
      method: "POST",
      body: JSON.stringify({ chat_id: Number($("summary-chat").value), days: Number($("summary-days").value) }),
    });
    $("digest-text").textContent = data.text;
    $("digest-meta").textContent = "Предпросмотр. В чат ничего не отправлено.";
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
      select.append(el("option", { value: option, textContent: option || "не задано", selected: option === field.value }));
    }
    return select;
  }
  return el("input", {
    id,
    type: field.kind === "secret" ? "password" : "text",
    autocomplete: "off",
    disabled: field.locked,
    value: field.kind === "secret" ? "" : field.value,
    placeholder: field.kind === "secret" ? (field.is_set ? "Задан. Оставьте пустым, чтобы не менять" : "Не задан") : "",
  });
}

function renderField(field) {
  const id = `f-${field.key}`;
  const note = field.locked ? "Значение задано на сервере и здесь не меняется." : field.help;
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
          el("label", { htmlFor: "confirm-external", textContent: "Владелец данных разрешил отправлять переписку этому сервису." }),
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
  status.textContent = "Сохраняю…";
  try {
    const result = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ values: collectValues(), confirm_external: $("confirm-external").checked }),
    });
    if (result.restart_needed) showRestart("Настройки сохранены. Они заработают после перезапуска бота.");
    await loadSettings();
    status.textContent = result.changed.length ? `Сохранено настроек: ${result.changed.length}` : "Изменений нет.";
  } catch (error) {
    const errors = error.detail && error.detail.errors;
    if (errors) {
      for (const [key, message] of Object.entries(errors)) {
        const target = $(`e-${key}`);
        if (target) target.textContent = message;
      }
      status.textContent = "Исправьте отмеченные поля.";
    } else {
      status.textContent = error.message;
    }
  }
}

// --- Step 1: model ----------------------------------------------------------

function stackCard(stack, installed) {
  const place = stack.external ? "Внешний сервис" : "На этом стенде";
  const missing = !stack.external && installed.length && !installed.includes(stack.model);
  const confirmBox = el("input", { id: `confirm-${stack.id}`, type: "checkbox" });
  const status = el("span", { role: "status" });
  const apply = stack.active ? null : el("button", { type: "button", textContent: "Выбрать" });
  if (apply) {
    apply.addEventListener("click", async () => {
      status.textContent = "Применяю…";
      try {
        await api(`/api/stacks/${stack.id}/apply`, {
          method: "POST",
          body: JSON.stringify({ confirm_external: confirmBox.checked }),
        });
        showRestart("Модель выбрана. Она заработает после перезапуска бота.");
        await loadStacks();
      } catch (error) {
        status.textContent = error.message;
      }
    });
  }
  const remove = stack.builtin ? null : el("button", { type: "button", textContent: "Удалить" });
  if (remove) {
    remove.addEventListener("click", async () => {
      await api(`/api/stacks/${stack.id}`, { method: "DELETE" });
      await loadStacks();
    });
  }
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: stack.name }),
      el("span", { class: `pill ${stack.active ? "on" : "off"}`, textContent: stack.active ? "Используется" : place }),
      apply,
      remove,
    ),
    missing ? el("p", { class: "error", textContent: "Модель не скачана: скачайте её в разделе ниже." }) : null,
    stack.external && !stack.active
      ? el("div", { class: "confirm" }, confirmBox,
          el("label", { htmlFor: confirmBox.id, textContent: "Владелец данных разрешил отправлять переписку этому сервису." }))
      : null,
    status,
  );
}

async function loadStacks() {
  const [data, { models }] = await Promise.all([api("/api/stacks"), api("/api/models")]);
  $("stacks").replaceChildren(...data.stacks.map((stack) => stackCard(stack, models)));
  $("models-installed").textContent = models.length
    ? `Уже скачаны: ${models.join(", ")}`
    : "Ollama не отвечает или в ней нет моделей.";
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
    $("stack-status").textContent = "Добавлено.";
    await loadStacks();
  } catch (error) {
    const errors = error.detail && error.detail.errors;
    if (errors) {
      for (const [key, message] of Object.entries(errors)) $(`se-${key}`).textContent = message;
      $("stack-status").textContent = "Исправьте отмеченные поля.";
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
    status.textContent = state.status === "running" ? `Скачиваю ${state.model}: ${state.percent}%` : state.error || "Модель скачана.";
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
  showRestart("Список чатов изменён. Он заработает после перезапуска бота.");
  await loadChats();
}

function waitingCard(chat) {
  const allow = el("button", { type: "button", class: "primary", textContent: "Разрешить" });
  allow.addEventListener("click", () => setAccess(chat.telegram_chat_id, true));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || "Без названия" }),
      el("span", { class: "pill off", textContent: "Ждёт разрешения" }),
      allow,
    ),
  );
}

function allowedEmptyCard(chat) {
  const forbid = el("button", { type: "button", textContent: "Запретить" });
  forbid.addEventListener("click", () => setAccess(chat.telegram_chat_id, false));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: String(chat.telegram_chat_id) }),
      el("span", { class: "pill on", textContent: "Разрешён, сообщений пока нет" }),
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
  const target = el("input", { id: `${prefix}-target`, value: chat.digest_chat_id ?? "", placeholder: "Этот чат" });
  const status = el("span", { role: "status" });
  const save = el("button", { type: "button", class: "primary", textContent: "Сохранить" });

  save.addEventListener("click", async () => {
    const raw = target.value.trim();
    const body = {
      timezone: tz.value.trim(),
      digest_time: time.value.trim(),
      digest_enabled: daily.checked,
      weekly_digest_enabled: weekly.checked,
      weekly_digest_day: Number(day.value),
    };
    if (raw) body.digest_chat_id = Number(raw);
    else body.clear_digest_chat = true;
    status.textContent = "Сохраняю…";
    try {
      await api(`/api/chats/${chat.id}`, { method: "PATCH", body: JSON.stringify(body) });
      status.textContent = "Сохранено.";
    } catch (error) {
      status.textContent = error.message;
    }
  });

  const access = el("button", {
    type: "button",
    class: chat.allowed ? "" : "primary",
    textContent: chat.allowed ? "Запретить" : "Разрешить",
  });
  access.addEventListener("click", () => setAccess(chat.telegram_chat_id, !chat.allowed));

  // Deleting is permanent, so the first press only arms the button.
  const purge = el("button", { type: "button", textContent: "Очистить данные чата" });
  let armed = false;
  purge.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      purge.textContent = "Удалить сообщения, темы и отчёты?";
      return;
    }
    try {
      const result = await api(`/api/chats/${chat.id}/purge`, { method: "POST", body: JSON.stringify({ confirm: true }) });
      status.textContent = `Удалено сообщений: ${result.removed}`;
      purge.textContent = "Очистить данные чата";
      armed = false;
    } catch (error) {
      status.textContent = error.message;
    }
  });

  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || "Без названия" }),
      el("span", { class: `pill ${chat.allowed ? "on" : "off"}`, textContent: chat.allowed ? "Разрешён" : "Не разрешён" }),
      access,
    ),
    el("details", {},
      el("summary", { textContent: `Отчёты: ${chat.digest_enabled ? `каждый день в ${chat.digest_time}` : "выключены"}` }),
      el("div", { class: "stack" },
        el("div", { class: "grid2" },
          el("div", { class: "field" }, el("label", { htmlFor: time.id, textContent: "Время отчёта" }), time),
          el("div", { class: "field" }, el("label", { htmlFor: tz.id, textContent: "Часовой пояс" }), tz),
          el("div", { class: "field" }, el("label", { htmlFor: day.id, textContent: "День еженедельного отчёта" }), day),
          el("div", { class: "field" }, el("label", { htmlFor: target.id, textContent: "Куда отправлять" }), target),
        ),
        el("div", { class: "check-row" }, daily, el("label", { htmlFor: daily.id, textContent: "Ежедневный отчёт" })),
        el("div", { class: "check-row" }, weekly, el("label", { htmlFor: weekly.id, textContent: "Еженедельный отчёт" })),
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
    ...(cards.length || data.waiting.length ? cards : [el("p", { class: "hint", textContent: "Чатов пока нет." })]),
  );
}

function presenceRow(chat) {
  const labels = { in: "ЕСТЬ", out: "НЕТ", moved: "НОВЫЙ ID" };
  const marks = { in: "ok", out: "fail", moved: "warn" };
  const note = chat.state === "moved"
    ? `Группа стала супергруппой, её новый ID: ${chat.moved_to}.`
    : chat.state === "in" && !chat.allowed ? "Бот здесь, но чат не разрешён." : "";
  const targetId = chat.state === "moved" ? chat.moved_to : chat.telegram_chat_id;
  const canAllow = (chat.state === "in" && !chat.allowed) || chat.state === "moved";
  const allow = canAllow ? el("button", { type: "button", class: "small", textContent: "Разрешить" }) : null;
  if (allow) allow.addEventListener("click", () => setAccess(targetId, true));
  return el("li", {},
    el("span", { class: `mark ${marks[chat.state]}`, textContent: labels[chat.state] }),
    el("span", {}, `${chat.title || chat.telegram_chat_id}${chat.allowed ? " (разрешён)" : ""} `, allow),
    note ? el("small", { textContent: note }) : null,
  );
}

async function checkPresence() {
  const button = $("presence");
  const status = $("presence-status");
  button.disabled = true;
  status.textContent = "Спрашиваю Telegram…";
  try {
    const data = await api("/api/chats/presence", { method: "POST" });
    $("presence-results").replaceChildren(...data.chats.map(presenceRow));
    status.textContent = data.chats.length
      ? `Бот @${data.bot}. Telegram не даёт полный список чатов, здесь только известные.`
      : `Бот @${data.bot}. Известных чатов нет: добавьте его в группу, пока он запущен.`;
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
  button.textContent = "Проверяю…";
  const list = $("check-results");
  try {
    const data = await api("/api/check", { method: "POST" });
    const labels = { ok: "ОК", warn: "ВНИМАНИЕ", fail: "ОШИБКА" };
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
    button.textContent = "Проверить всё";
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
        const details = [item.owner && `исполнитель: ${item.owner}`, item.deadline && `срок: ${item.deadline}`].filter(Boolean);
        return el("li", { class: item.status === "open" ? "" : "muted-item" },
          el("span", { class: "kind", textContent: `${KINDS[item.kind] || item.kind}: ` }),
          `${item.text}${details.length ? ` (${details.join(", ")})` : ""}`,
        );
      })),
    ),
  );
  const notes = [];
  if (data.rejected) notes.push(`отброшено проверками: ${data.rejected}`);
  if (data.failed) notes.push(`не обработано сообщений: ${data.failed}`);
  return el("article", { class: "result" },
    el("div", { class: "result-head" },
      document.createTextNode(data.stack),
      el("span", { textContent: `${data.model} · ${data.seconds} с` }),
    ),
    notes.length ? el("p", { class: "error", textContent: notes.join("; ") }) : null,
    ...(topics.length ? topics : [el("p", { class: "hint", textContent: "Рабочих тем не найдено." })]),
    el("details", {}, el("summary", { textContent: "Как будет выглядеть отчёт" }), el("pre", { class: "digest", textContent: data.digest })),
  );
}

async function runSandbox() {
  const button = $("sandbox-run");
  const status = $("sandbox-status");
  button.disabled = true;
  status.textContent = "Модель разбирает переписку. Без видеокарты это может занять несколько минут.";
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
    status.textContent = "Готово.";
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
    fact("Сообщений", String(data.messages)),
    fact("Чатов", String(data.chats)),
    fact("Размер базы", data.database_included ? `${data.database_mb} МБ` : "не входит в архив"),
  );
  $("move-warning").hidden = data.database_included;
}

async function exportBundle(event) {
  event.preventDefault();
  const status = $("export-status");
  status.textContent = "Собираю архив…";
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("export-password").value }),
  });
  $("export-password").value = "";
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    status.textContent = typeof data.detail === "string" ? data.detail : "Архив не собран.";
    return;
  }
  const name = (response.headers.get("content-disposition") || "").match(/filename="?([^";]+)/);
  const link = el("a", { href: URL.createObjectURL(await response.blob()), download: name ? name[1] : "digest-bot.zip" });
  link.click();
  URL.revokeObjectURL(link.href);
  status.textContent = "Архив скачан.";
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
  $("restart-text").textContent = "Запрос отправлен. Бот перезапустится в течение нескольких секунд.";
  $("restart").hidden = true;
});

api("/api/session")
  .then((state) => (state.authenticated ? showApp() : showLogin()))
  .catch(showLogin);
