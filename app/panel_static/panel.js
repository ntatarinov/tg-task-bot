"use strict";

// Everything from the server goes in through textContent: chat titles are written by
// Telegram users, so nothing here may build HTML from strings.
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
  await openTab("overview");
}

async function openTab(name) {
  for (const button of document.querySelectorAll("#tabs button")) {
    if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const tab of ["overview", "sandbox", "stacks", "settings", "chats", "move"]) $(`tab-${tab}`).hidden = tab !== name;
  if (name === "move") await loadMove();
  if (name === "overview") await loadOverview();
  if (name === "sandbox") await loadSandbox();
  if (name === "stacks") await loadStacks();
  if (name === "settings") await loadSettings();
  if (name === "chats") await loadChats();
}

function fact(label, value) {
  return el("div", { class: "fact" }, el("span", { textContent: label }), el("strong", { textContent: value }));
}

function showRestart(text) {
  $("restart-text").textContent = text;
  $("restart-bar").hidden = false;
}

async function loadOverview() {
  const data = await api("/api/overview");
  const where = { local: "на этом сервере", external: "внешний сервис", mock: "тестовый режим" }[data.model_location];
  $("summary").replaceChildren(
    fact("Модель", data.model),
    fact("Где работает", where),
    fact("Анализ", data.analysis_blocked ? `на паузе: ${data.analysis_blocked}` : "работает"),
    fact("Сообщений в очереди", String(data.pending)),
    fact("Разрешённых чатов", String(data.allowed_chats)),
  );
  if (data.restart_pending) showRestart("Бот перезапускается. Если сообщение не исчезает, проверьте, что бот запущен.");
}

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
    button.textContent = "Проверить";
  }
}

let settingFields = [];

function renderControl(field, models) {
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
  const input = el("input", {
    id,
    type: field.kind === "secret" ? "password" : "text",
    autocomplete: "off",
    disabled: field.locked,
    value: field.kind === "secret" ? "" : field.value,
    placeholder: field.kind === "secret" ? (field.is_set ? "Задан. Оставьте пустым, чтобы не менять" : "Не задан") : "",
  });
  if (field.key === "OPENAI_MODEL" && models.length) {
    input.setAttribute("list", "installed-models");
  }
  return input;
}

function renderField(field, models) {
  const id = `f-${field.key}`;
  const note = field.locked ? "Значение задано на сервере и здесь не меняется." : field.help;
  const error = el("p", { id: `e-${field.key}`, class: "error", role: "alert" });
  if (field.kind === "bool") {
    const parts = [
      el("div", { class: "check-row" }, renderControl(field, models), el("label", { htmlFor: id, textContent: field.label })),
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
    renderControl(field, models),
    note ? el("small", { textContent: note }) : null,
    error,
  );
}

async function loadSettings() {
  const [{ fields }, { models }] = await Promise.all([api("/api/settings"), api("/api/models")]);
  settingFields = fields;
  const groups = new Map();
  for (const field of fields) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  }
  const nodes = [];
  for (const [group, items] of groups) {
    nodes.push(el("fieldset", {}, el("legend", { textContent: group }), ...items.map((f) => renderField(f, models))));
  }
  const datalist = el("datalist", { id: "installed-models" }, ...models.map((m) => el("option", { value: m })));
  $("fields").replaceChildren(datalist, ...nodes);
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
    status.textContent = result.changed.length ? `Сохранено настроек: ${result.changed.length}` : "Изменений нет.";
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

  const access = el("button", { type: "button", textContent: chat.allowed ? "Запретить" : "Разрешить" });
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

  const lastDigest = chat.last_digest_at ? new Date(chat.last_digest_at).toLocaleString("ru-RU") : "ещё не было";
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || "Без названия" }),
      el("code", { textContent: String(chat.telegram_chat_id) }),
      el("span", { class: `pill ${chat.allowed ? "on" : "off"}`, textContent: chat.allowed ? "Разрешён" : "Не разрешён" }),
    ),
    el("div", { class: "grid2" },
      el("div", { class: "field" }, el("label", { htmlFor: tz.id, textContent: "Часовой пояс" }), tz),
      el("div", { class: "field" }, el("label", { htmlFor: time.id, textContent: "Время отчёта" }), time),
      el("div", { class: "field" }, el("label", { htmlFor: day.id, textContent: "День еженедельного отчёта" }), day),
      el("div", { class: "field" }, el("label", { htmlFor: target.id, textContent: "Куда отправлять" }), target),
    ),
    el("div", { class: "check-row" }, daily, el("label", { htmlFor: daily.id, textContent: "Ежедневный отчёт" })),
    el("div", { class: "check-row" }, weekly, el("label", { htmlFor: weekly.id, textContent: "Еженедельный отчёт" })),
    el("div", { class: "chat-foot" }, save, access, purge, status),
    el("div", { class: "chat-foot" },
      el("span", { textContent: `В очереди: ${chat.pending}` }),
      el("span", { textContent: `Последний отчёт: ${lastDigest}` }),
    ),
  );
}

async function setAccess(telegramChatId, allowed) {
  await api("/api/chats/access", {
    method: "POST",
    body: JSON.stringify({ telegram_chat_id: telegramChatId, allowed }),
  });
  showRestart("Список чатов изменён. Он заработает после перезапуска бота.");
  await loadChats();
}

function waitingCard(chat, note) {
  const allow = el("button", { type: "button", class: "primary", textContent: "Разрешить" });
  allow.addEventListener("click", () => setAccess(chat.telegram_chat_id, true));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: chat.title || "Без названия" }),
      el("code", { textContent: String(chat.telegram_chat_id) }),
      el("span", { class: "pill off", textContent: "Ждёт разрешения" }),
    ),
    el("p", { class: "hint", textContent: note }),
    el("div", { class: "chat-foot" }, allow),
  );
}

function allowedEmptyCard(chat) {
  const forbid = el("button", { type: "button", textContent: "Запретить" });
  forbid.addEventListener("click", () => setAccess(chat.telegram_chat_id, false));
  return el("article", { class: "chat" },
    el("div", { class: "chat-head" },
      el("h3", { textContent: "Сообщений пока нет" }),
      el("code", { textContent: String(chat.telegram_chat_id) }),
      el("span", { class: "pill on", textContent: "Разрешён" }),
    ),
    el("div", { class: "chat-foot" }, forbid),
  );
}

async function loadChats() {
  const data = await api("/api/chats");
  $("waiting").replaceChildren(
    ...data.waiting.map((chat) => waitingCard(chat, "Бот в этой группе, но пока её игнорирует.")),
  );
  const cards = [...data.chats.map(chatCard), ...data.allowed_without_messages.map(allowedEmptyCard)];
  $("chats").replaceChildren(
    ...(cards.length || data.waiting.length
      ? cards
      : [el("p", { class: "hint", textContent: "Чатов пока нет. Добавьте бота в группу и напишите там сообщение." })]),
  );
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
      el("span", { textContent: `${data.model} · ${data.seconds} с · сообщений: ${data.messages}` }),
    ),
    notes.length ? el("p", { class: "error", textContent: notes.join("; ") }) : null,
    ...(topics.length ? topics : [el("p", { class: "hint", textContent: "Рабочих тем не найдено." })]),
    el("details", {}, el("summary", { textContent: "Как будет выглядеть дайджест" }), el("pre", { textContent: data.digest })),
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

function stackCard(stack, installed) {
  const place = stack.external ? "Внешний сервис" : "Локально";
  const missing = !stack.external && installed.length && !installed.includes(stack.model);
  const confirmBox = el("input", { id: `confirm-${stack.id}`, type: "checkbox" });
  const status = el("span", { role: "status" });
  const apply = el("button", { type: "button", class: "primary", textContent: "Применить", disabled: stack.active });
  apply.addEventListener("click", async () => {
    status.textContent = "Применяю…";
    try {
      await api(`/api/stacks/${stack.id}/apply`, {
        method: "POST",
        body: JSON.stringify({ confirm_external: confirmBox.checked }),
      });
      showRestart("Вариант применён. Он заработает после перезапуска бота.");
      await loadStacks();
    } catch (error) {
      status.textContent = error.message;
    }
  });
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
    ),
    el("code", { textContent: `${stack.model} · ${stack.base_url || "api.openai.com"}` }),
    missing ? el("p", { class: "error", textContent: "Модель не скачана. Скачайте её ниже." }) : null,
    stack.external && !stack.active
      ? el("div", { class: "confirm" }, confirmBox,
          el("label", { htmlFor: confirmBox.id, textContent: "Владелец данных разрешил отправлять переписку этому сервису." }))
      : null,
    el("div", { class: "chat-foot" }, apply, remove, status),
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

async function loadMove() {
  const data = await api("/api/migration");
  $("move-summary").replaceChildren(
    fact("Сообщений", String(data.messages)),
    fact("Чатов", String(data.chats)),
    fact("Размер базы", data.database_included ? `${data.database_mb} МБ` : "не входит в архив"),
    fact("Своих вариантов стека", String(data.custom_stacks)),
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

$("check").addEventListener("click", runChecks);
$("settings-form").addEventListener("submit", saveSettings);
$("sandbox-run").addEventListener("click", runSandbox);
$("export-form").addEventListener("submit", exportBundle);
$("sandbox-stack").addEventListener("change", updateSandboxConsent);
$("stack-form").addEventListener("submit", addStack);
$("pull-form").addEventListener("submit", pullModel);

$("restart").addEventListener("click", async () => {
  await api("/api/restart", { method: "POST" });
  $("restart-text").textContent = "Запрос отправлен. Бот перезапустится в течение нескольких секунд.";
  $("restart").hidden = true;
});

api("/api/session")
  .then((state) => (state.authenticated ? showApp() : showLogin()))
  .catch(showLogin);
