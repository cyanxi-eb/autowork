/* Render inert Markdown using text nodes and explicitly permitted links. */
function renderMarkdown(container, source, openRelative) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const result = document.createDocumentFragment();
  const make = (tag, text) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    return element;
  };
  function inline(element, text) {
    const tokens = /`([^`]+)`|\[([^\]\n]+)\]\(([^\s()]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|~~([^~]+)~~/g;
    let offset = 0;
    for (const match of text.matchAll(tokens)) {
      const [value, code, label, href, strong, em, strike] = match;
      element.append(text.slice(offset, match.index));
      if (code) element.append(make("code", code));
      else if (label) {
        const link = make("a", label);
        if (/^https?:\/\//i.test(href)) {
          link.href = href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          element.append(link);
        } else if (openRelative && !/^(?:[a-z][a-z\d+.-]*:|[/\\]|#)/i.test(href)) {
          const button = make("button", label);
          button.type = "button";
          button.className = "markdown-link";
          button.addEventListener("click", () => openRelative(href));
          element.append(button);
        } else element.append(label);
      } else element.append(make(strong ? "strong" : em ? "em" : "del", strong ?? em ?? strike));
      offset = match.index + value.length;
    }
    element.append(text.slice(offset));
    return element;
  }
  const paragraph = (tag, text) => inline(make(tag), text);
  const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
  const fence = /^\s*(`{3,}|~{3,})(.*)$/;
  const rule = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const item = /^\s*([-+*]|\d+[.)])\s+(.+)$/;
  const divider = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
  const quote = /^\s*>\s?(.*)$/;
  const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
  const blockStart = (line, next) => !line?.trim() || heading.test(line) || fence.test(line)
    || rule.test(line) || item.test(line) || quote.test(line) || divider.test(next ?? "");

  let index = 0;
  while (index < lines.length) {
    const line = lines[index++];
    if (!line.trim()) continue;
    const opening = fence.exec(line);
    const title = heading.exec(line);
    const bullet = item.exec(line);
    if (opening) {
      const body = [];
      const marker = opening[1];
      while (index < lines.length) {
        const line = lines[index++];
        if (line.trim().startsWith(marker) && [...line.trim()].every((character) => character === marker[0])) break;
        body.push(line);
      }
      const pre = make("pre");
      pre.append(make("code", body.join("\n")));
      result.append(pre);
    } else if (title) {
      result.append(paragraph("h" + title[1].length, title[2]));
    } else if (rule.test(line)) {
      result.append(make("hr"));
    } else if (bullet) {
      const ordered = /^\d/.test(bullet[1]);
      const list = make(ordered ? "ol" : "ul");
      if (ordered) list.start = Number.parseInt(bullet[1], 10);
      list.append(paragraph("li", bullet[2]));
      let next = item.exec(lines[index] ?? "");
      while (next && /^\d/.test(next[1]) === ordered) {
        list.append(paragraph("li", next[2]));
        next = item.exec(lines[++index] ?? "");
      }
      result.append(list);
    } else if (quote.test(line)) {
      const parts = [quote.exec(line)[1]];
      while (quote.test(lines[index] ?? "")) parts.push(quote.exec(lines[index++])[1]);
      const block = make("blockquote");
      block.append(paragraph("p", parts.join(" ")));
      result.append(block);
    } else if (line.includes("|") && divider.test(lines[index] ?? "")) {
      index++;
      const table = make("table");
      const head = table.createTHead().insertRow();
      const header = cells(line);
      head.append(...header.map((cell) => paragraph("th", cell)));
      const tbody = table.createTBody();
      while (lines[index]?.includes("|") && !blockStart(lines[index], lines[index + 1])) {
        const row = cells(lines[index++]);
        tbody.insertRow().append(...header.map((_, column) => paragraph("td", row[column] ?? "")));
      }
      const scroll = make("div");
      scroll.className = "markdown-table";
      scroll.append(table);
      result.append(scroll);
    } else {
      const parts = [line.trim()];
      while (index < lines.length && !blockStart(lines[index], lines[index + 1])) parts.push(lines[index++].trim());
      result.append(paragraph("p", parts.join("\n")));
    }
  }
  container.replaceChildren(result);
}
