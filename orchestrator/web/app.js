const byId = (id) => document.getElementById(id);
const workspace = byId("workspace");
const projectSelector = byId("projects");
const composer = byId("composer");
const messageInput = byId("message");
const createDialog = byId("create-dialog");
const connectionDialog = byId("connection-dialog");
const contextDialog = byId("context-dialog");
const compact = matchMedia("(max-width: 880px)");
const sides = ["left", "right"];
const defaults = { left: "dsh", right: "codex" };
const agentMarks = { deepseek: "DS", dsh: "DSH", codex: "CX" };
const titleByStage = { intake: "需求收集", review: "模拟复核" };
const demoLabels = { waiting_input: "等待确认", running: "运行中", paused: "已暂停", completed: "已完成", blocked: "已阻塞" };
const clock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" });
const state = { agents: [], projects: [], active: null, layout: {}, mobileSide: null };
const connectionFields = {
  base_url: ["API 基础地址", "url", "https://api.deepseek.com"],
  key_env: ["密钥环境变量名", "text", "DEEPSEEK_API_KEY"],
  command: ["CLI 命令或可执行文件路径", "text", ""],
  model: ["模型", "text", "留空使用适配器默认值"],
  effort: ["思考档位", "text", "留空使用适配器默认值"],
};

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败。");
  return data;
}

function loadPreference(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function savePreference(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Settings remain usable for the current page when storage is unavailable.
  }
}

function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const button = byId("theme-toggle");
  button.textContent = dark ? "亮色" : "暗色";
  button.title = dark ? "切换到亮色" : "切换到暗色";
  button.setAttribute("aria-pressed", String(dark));
}

function layoutKey() {
  return `workbench.layout.${state.active?.project.id ?? "empty"}`;
}

function note(text) {
  byId("notice").textContent = text;
}

function node(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

function renderProjects() {
  projectSelector.replaceChildren(new Option("选择项目", ""), ...state.projects.map(
    (project) => new Option(project.name, project.id),
  ));
  projectSelector.value = state.active?.project.id ?? "";
}

function updateLayout() {
  for (const side of sides) {
    const open = compact.matches ? state.mobileSide === side : state.layout[side].open;
    byId(`${side}-panel`).hidden = !open;
    byId(`toggle-${side}`).setAttribute("aria-expanded", String(open));
    workspace.dataset[side] = String(open);
  }
}

function messagesIn(channel) {
  return (state.active?.messages ?? []).filter((event) => event.data.channel === channel);
}

function authorName(id) {
  if (id === "user") return "你";
  if (id === "scheduler") return "项目调度器";
  return state.agents.find((agent) => agent.id === id)?.name ?? id;
}

function entry(event) {
  const message = event.data;
  const element = node("article", "", `entry ${message.author}`);
  const header = document.createElement("header");
  const time = node("time", clock.format(new Date(event.occurred_at)));
  time.dateTime = event.occurred_at;
  header.append(node("strong", authorName(message.author)), time);
  element.append(header, node("p", message.text));
  return element;
}

function renderMessages(container, messages, emptyText, heading = "") {
  const key = JSON.stringify([messages.map((event) => event.seq), emptyText, heading]);
  if (container.dataset.renderKey === key) return;
  const oldTop = container.scrollTop;
  const nearEnd = container.scrollHeight - oldTop - container.clientHeight < 30;
  container.dataset.renderKey = key;
  if (messages.length) {
    container.replaceChildren(...messages.map(entry));
    container.scrollTop = nearEnd ? container.scrollHeight : oldTop;
    return;
  }
  const empty = node("div", "", "empty");
  if (heading) empty.append(node("h2", heading));
  empty.append(node("p", emptyText));
  container.replaceChildren(empty);
}

function renderConversation() {
  const project = state.active?.project;
  const run = state.active?.run;
  composer.hidden = !project || project.demo;
  byId("stage").hidden = !project;
  byId("project-memory").hidden = !project;
  byId("stage").textContent = run ? state.demo.steps[run.step] ?? "已完成" : titleByStage[project?.stage] ?? "";
  renderMessages(
    byId("conversation"), messagesIn("deepseek"),
    project ? "正在读取项目对话。" : "打开演示体验从需求到验收，或新建项目记录自己的需求。",
    project ? "" : "从一个需求开始",
  );
}

function renderDemo() {
  const active = state.active;
  const run = active?.run;
  byId("demo-strip").hidden = !run;
  byId("demo-choices").hidden = run?.status !== "waiting_input";
  if (!run) return;

  const status = byId("demo-status");
  status.textContent = demoLabels[run.status];
  status.dataset.status = run.status;
  const round = run.round ?? (run.step >= 5 ? 2 : 1);
  const roundLabel = byId("demo-round");
  roundLabel.hidden = run.step < 3;
  roundLabel.textContent = `第 ${round} 轮 · rev-${String(round).padStart(2, "0")}`;
  const steps = byId("demo-steps");
  const progressKey = `${run.step}/${round}`;
  if (steps.dataset.step !== progressKey) {
    steps.dataset.step = progressKey;
    steps.replaceChildren(...state.demo.steps.map((label, index) => {
      const skipped = round === 1 && run.step > 6 && (index === 5 || index === 6);
      const completed = index < run.step && !skipped;
      const current = index === run.step;
      const item = node("li", "", completed ? "done" : current ? "current" : "");
      item.append(node("span", completed ? "✓" : skipped ? "–" : String(index + 1), "step-index"), node("span", label));
      if (current) item.setAttribute("aria-current", "step");
      if (skipped) item.setAttribute("aria-label", `${label}已跳过`);
      return item;
    }));
    const current = steps.querySelector(".current");
    if (current && current.offsetLeft + current.offsetWidth > steps.clientWidth + steps.scrollLeft) {
      steps.scrollLeft = current.offsetLeft - 12;
    } else if (run.step === 0) {
      steps.scrollLeft = 0;
    }
  }

  const artifactsButton = byId("artifacts");
  artifactsButton.textContent = `交付物 ${active.artifacts.size}`;
  artifactsButton.disabled = active.artifacts.size === 0;
  const control = byId("demo-control");
  const controls = { running: ["pause", "暂停"], paused: ["resume", "继续"], completed: ["replay", "再跑一次"], blocked: ["replay", "另开演示"] };
  const selected = controls[run.status];
  control.hidden = !selected;
  if (selected) {
    [control.dataset.action, control.textContent] = selected;
    control.disabled = Boolean(active.controlling);
  }
  for (const button of byId("demo-choices").querySelectorAll("button")) {
    button.disabled = Boolean(active.controlling);
  }
}

function renderArtifacts() {
  const active = state.active;
  if (!active?.artifacts.size) return;
  const key = active.artifacts.has(active.selectedArtifact) ? active.selectedArtifact : [...active.artifacts.keys()].at(-1);
  const selected = active.artifacts.get(key);
  byId("artifact-list").replaceChildren(...Array.from(active.artifacts, ([path, artifact]) => {
    const button = node("button", artifact.title);
    button.type = "button";
    button.setAttribute("aria-pressed", String(path === key));
    button.addEventListener("click", () => {
      active.selectedArtifact = path;
      renderArtifacts();
      byId("artifact-list").querySelector('[aria-pressed="true"]')?.focus();
    });
    return button;
  }));
  byId("artifact-name").textContent = selected.title;
  byId("artifact-path").textContent = selected.path;
  byId("artifact-content").textContent = selected.content;
}

function renderDock(side) {
  const panel = byId(`${side}-panel`);
  const config = state.layout[side];
  const agent = state.agents.find((item) => item.id === config.agent);
  const chooser = panel.querySelector(".agent-select");
  chooser.value = config.agent;
  const icon = panel.querySelector(".agent-icon");
  icon.className = `agent-icon ${agent?.id ?? ""}`;
  icon.textContent = agentMarks[config.agent] ?? agent?.name.slice(0, 2).toUpperCase() ?? "";
  panel.querySelector(".agent-role").textContent = agent?.role ?? "";
  const connection = panel.querySelector(".dock-connection");
  connection.textContent = config.agent === "deepseek" ? "API 接口" : "CLI 接口";
  connection.setAttribute("aria-label", `${agent?.name} 接口`);
  panel.querySelector(".dock-persona").setAttribute("aria-label", `${agent?.name} 人设`);

  const allMessages = messagesIn(config.agent);
  const sessions = new Map();
  for (const event of allMessages) {
    const id = event.data.session_id;
    if (!sessions.has(id)) sessions.set(id, []);
    sessions.get(id).push(event);
  }
  const sessionChooser = panel.querySelector(".session-select");
  sessionChooser.replaceChildren(new Option(`全部会话${sessions.size ? ` · ${sessions.size}` : ""}`, ""), ...Array.from(sessions).reverse().map(
    ([id, events]) => new Option(events[0].data.session_title, id),
  ));
  sessionChooser.value = sessions.has(config.session) ? config.session : "";
  const focus = sessionChooser.value;
  const visible = focus ? [[focus, sessions.get(focus)]] : [...sessions];
  panel.querySelector(".candidate").textContent = (focus ? sessions.get(focus).at(-1) : allMessages.at(-1))?.data.candidate ?? "";
  renderDockHistory(panel.querySelector(".dock-messages"), visible, config.agent, focus, state.active?.disclosures[side]);
}

function renderDockHistory(container, sessions, agent, focus, disclosures) {
  const scope = JSON.stringify([state.active?.project.id, agent, focus]);
  const key = JSON.stringify([scope, sessions.map(([, events]) => events.map(({ seq }) => seq))]);
  if (container.dataset.historyKey === key) return;
  const sameScope = container.dataset.historyScope === scope;
  const scrollTop = sameScope ? container.scrollTop : 0;
  const follow = !sameScope || container.scrollHeight - scrollTop - container.clientHeight < 30;
  const wasOpen = new Map(Array.from(container.querySelectorAll("details"), (detail) => [detail.dataset.session, detail.open]));
  container.dataset.historyScope = scope;
  container.dataset.historyKey = key;
  if (!sessions.length) {
    container.replaceChildren(node("p", state.active ? "这个 Agent 暂无会话。" : "选择项目后查看 Agent 会话。", "empty"));
    return;
  }
  container.replaceChildren(...sessions.map(([id, events], index) => {
    const { session_title: title, candidate } = events.at(-1).data;
    const details = node("details", "", "session-record");
    details.dataset.session = id;
    const key = `${agent}/${id}`;
    const latest = index === sessions.length - 1;
    const openByDefault = follow ? latest : (wasOpen.get(id) ?? false);
    details.open = disclosures?.get(key) ?? openByDefault;
    const summary = node("summary", "", "session-summary");
    summary.append(node("strong", title), node("span", candidate ?? `${events.length} 条`, "candidate"));
    summary.addEventListener("click", () => disclosures?.set(key, !details.open));
    const entries = node("div", "", "session-entries");
    entries.append(...events.map(entry));
    details.append(summary, entries);
    return details;
  }));
  container.scrollTop = follow && !focus ? container.scrollHeight : scrollTop;
}

function renderProject() {
  renderConversation();
  renderDemo();
  for (const side of sides) renderDock(side);
}

async function refreshEvents() {
  const active = state.active;
  if (!active || active.refreshing) return;
  active.refreshing = true;
  try {
    let data;
    do {
      data = await api(`/api/projects/${active.project.id}/events?after=${active.cursor}`);
      if (state.active !== active) return;
      active.cursor = data.cursor;
      for (const event of data.events) {
        if (event.kind === "message.added") active.messages.push(event);
        if (event.kind === "demo.changed") active.run = event.data;
        if (event.kind === "artifact.created") active.artifacts.set(event.data.path, event.data);
      }
      if (data.events.length) {
        renderProject();
        if (byId("artifact-dialog").open) renderArtifacts();
      }
    } while (data.events.length === 200);
    if (active.offline) note("");
    active.offline = false;
  } catch {
    if (state.active === active) {
      active.offline = true;
      note("项目事件暂未同步，正在重试。");
    }
  } finally {
    active.refreshing = false;
  }
}

function selectProject(id) {
  const selected = state.projects.find((project) => project.id === id);
  if (state.active?.project.id !== selected?.id) messageInput.value = "";
  if (byId("artifact-dialog").open) byId("artifact-dialog").close();
  if (contextDialog.open) contextDialog.close();
  state.active = selected ? {
    project: selected, messages: [], cursor: 0, artifacts: new Map(),
    disclosures: { left: new Map(), right: new Map() },
  } : null;
  state.mobileSide = null;
  const saved = loadPreference(layoutKey(), {});
  for (const side of sides) {
    const previous = saved[side];
    const hasAgent = state.agents.some((agent) => agent.id === previous?.agent);
    state.layout[side] = {
      open: previous?.open !== false,
      agent: hasAgent ? previous.agent : defaults[side],
      session: hasAgent ? previous.session : "",
    };
  }
  savePreference("workbench.active", id);
  renderProjects();
  updateLayout();
  renderProject();
  note("");
  void refreshEvents();
}

function setupDock(side) {
  const panel = byId(`${side}-panel`);
  panel.append(byId("dock-template").content.cloneNode(true));
  const chooser = panel.querySelector(".agent-select");
  chooser.replaceChildren(...state.agents.map((item) => new Option(item.name, item.id)));
  chooser.addEventListener("change", () => {
    state.layout[side].agent = chooser.value;
    state.layout[side].session = "";
    savePreference(layoutKey(), state.layout);
    renderDock(side);
  });
  const session = panel.querySelector(".session-select");
  session.addEventListener("change", () => {
    state.layout[side].session = session.value;
    savePreference(layoutKey(), state.layout);
    renderDock(side);
  });
  panel.querySelector(".dock-connection").addEventListener("click", () => openConnection(state.layout[side].agent));
  panel.querySelector(".dock-persona").addEventListener("click", () => openPersona(state.layout[side].agent));
  byId(`toggle-${side}`).addEventListener("click", () => {
    if (compact.matches) {
      state.mobileSide = state.mobileSide === side ? null : side;
    } else {
      state.layout[side].open = !state.layout[side].open;
      savePreference(layoutKey(), state.layout);
    }
    updateLayout();
  });
}

function openPersona(agentId) {
  const agent = state.agents.find((item) => item.id === agentId);
  if (agent) void openContextDocument(`${agent.name} 人设`, `/api/agents/${agentId}/persona`);
}

async function openContextDocument(title, url) {
  const request = {};
  state.contextRequest = request;
  byId("context-title").textContent = title;
  byId("context-path").textContent = "";
  byId("context-content").textContent = "正在读取…";
  contextDialog.showModal();
  try {
    const data = await api(url);
    if (state.contextRequest !== request || !contextDialog.open) return;
    byId("context-path").textContent = data.path;
    byId("context-content").textContent = data.content;
  } catch (error) {
    if (state.contextRequest === request && contextDialog.open) byId("context-content").textContent = error.message;
  }
}

async function openConnection(agentId) {
  const agent = state.agents.find((item) => item.id === agentId);
  if (!agent) return;
  const selection = { id: agentId };
  state.connection = selection;
  byId("connection-title").textContent = `${agent.name} 接口`;
  byId("connection-status").textContent = "正在读取配置…";
  byId("connection-error").hidden = true;
  byId("connection-fields").replaceChildren();
  byId("save-connection").disabled = true;
  connectionDialog.showModal();
  try {
    const data = await api(`/api/agents/${agentId}/connection`);
    if (state.connection === selection) renderConnection(data);
  } catch (error) {
    if (state.connection === selection) connectionError(error.message);
  }
}

function renderConnection(data) {
  state.connection.ready = true;
  const credential = data.key_available ? "已检测到密钥环境变量。" : "尚未检测到密钥环境变量。";
  byId("connection-status").textContent = `${data.kind === "api" ? `HTTP API · ${credential}` : "本地 CLI。"} 当前运行模拟任务，填写项保存在此工作台。`;
  byId("connection-fields").replaceChildren(...Object.entries(data.settings).map(([name, value]) => {
    const [label, type, hint] = connectionFields[name];
    const group = node("div", "", "connection-field");
    const caption = node("label", label);
    const input = document.createElement("input");
    input.name = name;
    input.type = type;
    input.id = `connection-${name}`;
    input.value = value;
    input.placeholder = hint;
    input.required = ["base_url", "key_env", "command"].includes(name);
    input.maxLength = name === "base_url" || name === "command" ? 1024 : 128;
    input.spellcheck = false;
    caption.htmlFor = input.id;
    group.append(caption, input);
    return group;
  }));
  byId("save-connection").disabled = false;
}

function connectionError(message) {
  const error = byId("connection-error");
  error.textContent = message;
  error.hidden = false;
}

function openCreatedProject(project) {
  if (!state.projects.some((item) => item.id === project.id)) state.projects.unshift(project);
  selectProject(project.id);
}

async function demoAction(action, policy) {
  const active = state.active;
  if (!active?.run || active.controlling) return;
  active.controlling = true;
  renderDemo();
  try {
    if (action === "replay") {
      const project = await api("/api/demo", { fresh: true });
      if (state.active === active) openCreatedProject(project);
    } else {
      await api(`/api/projects/${active.project.id}/demo`, { action, policy });
      if (state.active === active) note("");
    }
  } catch (error) {
    if (state.active === active) note(error.message);
  } finally {
    active.controlling = false;
    if (state.active === active) {
      renderDemo();
      await refreshEvents();
    }
  }
}

async function boot() {
  const data = await api("/api/workbench");
  state.agents = data.agents;
  state.projects = data.projects;
  state.demo = data.demo;
  byId("demo-choices").replaceChildren(...Object.entries(state.demo.policies).map(([policy, label]) => {
    const button = node("button", label, policy === "keep" ? "primary" : "");
    button.type = "button";
    button.addEventListener("click", () => demoAction("confirm", policy));
    return button;
  }));
  for (const side of sides) setupDock(side);
  const previous = loadPreference("workbench.active", null);
  selectProject(state.projects.some(({ id }) => id === previous) ? previous : state.projects[0]?.id ?? "");
  setInterval(refreshEvents, 750);
  compact.addEventListener("change", updateLayout);
  projectSelector.addEventListener("change", () => selectProject(projectSelector.value));

  byId("new-project").addEventListener("click", () => createDialog.showModal());
  byId("cancel-create").addEventListener("click", () => createDialog.close());
  byId("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    const error = byId("create-error");
    button.disabled = true;
    error.hidden = true;
    try {
      const project = await api("/api/projects", {
        name: byId("project-name").value, request: byId("project-request").value,
      });
      createDialog.close();
      byId("create-form").reset();
      openCreatedProject(project);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  byId("demo").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      openCreatedProject(await api("/api/demo", {}));
    } catch (error) {
      note(error.message);
    } finally {
      button.disabled = false;
    }
  });

  byId("demo-control").addEventListener("click", (event) => demoAction(event.currentTarget.dataset.action));
  byId("artifacts").addEventListener("click", () => {
    renderArtifacts();
    byId("artifact-dialog").showModal();
  });
  byId("close-artifacts").addEventListener("click", () => byId("artifact-dialog").close());
  byId("deepseek-connection").addEventListener("click", () => openConnection("deepseek"));
  byId("deepseek-persona").addEventListener("click", () => openPersona("deepseek"));
  byId("project-memory").addEventListener("click", () => {
    const project = state.active?.project;
    if (project) void openContextDocument("项目记忆", `/api/projects/${project.id}/memory`);
  });
  byId("close-context").addEventListener("click", () => contextDialog.close());
  byId("close-connection").addEventListener("click", () => connectionDialog.close());
  connectionDialog.addEventListener("close", () => {
    if (!connectionDialog.open) state.connection = null;
  });
  byId("connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const connection = state.connection;
    if (!connection?.ready || connection.saving) return;
    connection.saving = true;
    const button = byId("save-connection");
    button.disabled = true;
    byId("connection-error").hidden = true;
    try {
      await api(`/api/agents/${connection.id}/connection`, Object.fromEntries(new FormData(event.currentTarget)));
      if (state.connection === connection) connectionDialog.close();
    } catch (error) {
      if (state.connection === connection) connectionError(error.message);
    } finally {
      connection.saving = false;
      if (state.connection === connection) button.disabled = false;
    }
  });

  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const active = state.active;
    const text = messageInput.value.trim();
    if (!active || !text) return;
    const button = composer.querySelector("button");
    button.disabled = true;
    try {
      await api(`/api/projects/${active.project.id}/messages`, { text });
      if (state.active === active) {
        if (messageInput.value.trim() === text) messageInput.value = "";
        note("");
        await refreshEvents();
      }
    } catch (error) {
      if (state.active === active) note(error.message);
    } finally {
      button.disabled = false;
    }
  });
}

applyTheme(loadPreference("workbench.theme", "light"));
byId("theme-toggle").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(theme);
  savePreference("workbench.theme", theme);
});
boot().catch(() => note("无法连接本机工作台。请确认服务已启动后刷新。"));
