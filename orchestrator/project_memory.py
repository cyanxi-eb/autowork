import re


START = "<!-- autowork:memory:start -->"
END = "<!-- autowork:memory:end -->"
BOUNDARIES = re.compile(rf"^({re.escape(START)}|{re.escape(END)})$", re.MULTILINE)

TEMPLATE = """# 项目记忆

## 人工备注

在这里补充已确认的决定、现场约束和交接事项。自动更新写入下方的标记区域。
"""

STATUSES = {
    "waiting_input": "待确认需求", "running": "运行中", "paused": "已暂停",
    "completed": "已交付", "blocked": "验收阻塞", "queued": "待派发", "interrupted": "执行待核实",
}
POLICIES = {"keep": "保留原记录", "update": "更新原记录"}


def checkpoint(project, events):
    last_run = None
    last_review = None
    live = None
    initial_request = None
    accepted = []
    artifacts = set()
    for event in events:
        kind, data = event["kind"], event["data"]
        if kind == "demo.changed":
            last_run = data
        elif kind == "live.changed":
            live = data
        elif kind == "message.added" and initial_request is None and data["author"] == "user" and data["channel"] == "deepseek":
            initial_request = " ".join(data["text"].split())
        elif kind == "test.completed":
            last_review = data
        elif kind == "stage.accepted":
            accepted.append(data)
        elif kind == "artifact.created":
            artifacts.add(data["path"])

    lines = ["## 自动续接摘要", "", f"- 项目编号 `{project['id']}`"]
    if initial_request:
        excerpt = initial_request[:320] + ("…" if len(initial_request) > 320 else "")
        lines.append(f"- 初始需求摘录（以需求档案为准） {excerpt}")
    if live:
        lines.append(f"- 实际运行状态 {STATUSES.get(live['status'], live['status'])}，第 {live['round']} 轮。")
        lines.append(f"- 当前进度 {live['detail']}")
        if live["candidate"]:
            lines.append(f"- 候选提交 {live['candidate']}。")
        delivery = f"runs/{live['id']}/delivery.md"
        if delivery in artifacts:
            lines.append(f"- [当前运行交付摘要]({delivery})")
    elif last_run:
        status = last_run["status"]
        phase = last_run.get("phase") or f"步骤 {last_run['step'] + 1}"
        lines.append(f"- 演示状态 {STATUSES.get(status, status)}，{phase}。")
        if last_run.get("round"):
            lines.append(f"- 当前候选轮次 {last_run['round']}。")
        policy = POLICIES.get(last_run.get("policy"))
        if policy:
            lines.append(f"- 已确认同编号再次导入时{policy}。")
        if status == "blocked":
            lines.append("- 续接时先核对最近复核，明确是否补证或重新安排返修。")
    else:
        lines.append("- 当前阶段为需求收集。" if project["stage"] == "intake" else "- 这是早期演示档案，可从历史会话查阅详情。")

    if last_review:
        candidate = last_review["candidate"]
        path = f"reviews/{candidate}.md"
        label = f"[{candidate}]({path})" if path in artifacts else f"`{candidate}`"
        verdict = "通过" if last_review["passed"] else "未通过"
        lines.append(f"- 最近复核 {label}，{verdict}。")
    for item in accepted[-4:]:
        lines.append(f"- 已验收 {item['title']}，候选 `{item['candidate']}`。")
    lines.extend(["", "## 关联档案", "", "- [需求档案](requirements/需求档案.md)", "- [实施进度](progress.md)"])
    for path, label in (("requirements/需求定稿-v1.md", "已确认需求"), ("environment/勘察记录.md", "勘察记录"),
                        ("plan/总体方案.md", "总体方案"), ("evidence/交付摘要.md", "交付摘要")):
        if path in artifacts:
            lines.append(f"- [{label}]({path})")
    return "\n".join(lines)


def merge_memory(current, summary):
    block = f"{START}\n{summary}\n{END}"
    markers = list(BOUNDARIES.finditer(current))
    if not markers:
        prefix = current or TEMPLATE
        return f"{prefix}\n\n{block}\n"
    if [marker.group() for marker in markers] != [START, END]:
        return current
    return current[:markers[0].start()] + block + current[markers[1].end():]
