const byId = (id) => document.getElementById(id);
const workspace = byId("workspace");
const projectSelector = byId("projects");
const composer = byId("composer");
const messageInput = byId("message");
const createDialog = byId("create-dialog");
const compact = matchMedia("(max-width: 880px)");
const sides = ["left", "right"];
const defaults = { left: "dsh", right: "codex" };
const agentMarks = { deepseek: "DS", dsh: "DSH", codex: "CX" };
const titleByStage = { intake: "需求收集", review: "模拟复核" };
const demoLabels = { waiting_input: "等待确认", running: "运行中", paused: "已暂停", completed: "已完成", blocked: "已阻塞" };
const clock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" });
const state = { agents: [], projects: [], active: null, layout: {}, mobileSide: null };

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
  const steps = byId("demo-steps");
  if (steps.dataset.step !== String(run.step)) {
    steps.dataset.step = run.step;
    steps.replaceChildren(...state.demo.steps.map((label, index) => {
      const completed = index < run.step;
      const current = index === run.step;
      const item = node("li", "", completed ? "done" : current ? "current" : "");
      item.append(node("span", completed ? "✓" : String(index + 1), "step-index"), node("span", label));
      if (current) item.setAttribute("aria-current", "step");
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

  const allMessages = messagesIn(config.agent);
  const sessions = new Map(allMessages.map(({ data }) => [data.session_id, data.session_title]));
  const sessionChooser = panel.querySelector(".session-select");
  sessionChooser.replaceChildren(new Option("跟随最新会话", ""), ...Array.from(sessions).reverse().map(
    ([id, title]) => new Option(title, id),
  ));
  sessionChooser.value = sessions.has(config.session) ? config.session : "";
  const selected = sessionChooser.value || allMessages.at(-1)?.data.session_id;
  const visible = allMessages.filter(({ data }) => data.session_id === selected);
  panel.querySelector(".candidate").textContent = visible.at(-1)?.data.candidate ?? "";
  renderMessages(panel.querySelector(".dock-messages"), visible,
    state.active ? "这个 Agent 暂无会话。" : "选择项目后查看 Agent 会话。");
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
  state.active = selected ? { project: selected, messages: [], cursor: 0, artifacts: new Map() } : null;
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

boot().catch(() => note("无法连接本机工作台。请确认服务已启动后刷新。"));
