// Markdown renderer for chat replies: a CommonMark / GFM subset with no dependencies.
//
// Headings, paragraphs, emphasis, inline code, fenced code (language label, copy button, light syntax colouring),
// block quotes, nested ordered / unordered / task lists, tables, horizontal rules, links, images and bare URLs.
// Every piece of text is escaped and only http(s) links and http(s) / data:image images get through, so neither
// model output nor fetched web content can inject HTML. Single newlines inside a paragraph become line breaks,
// the way chat UIs show them. Unclosed code fences render as code so a reply looks right while it streams.
"use strict";

const Markdown = (() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function safeUrl(u, image) {
    u = u.trim().replace(/^<(.*)>$/, "$1");
    if (/^https?:\/\/[^\s"'<>]+$/i.test(u)) return esc(u);
    if (image && /^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$/i.test(u)) return u;
    return null;
  }

  const link = (html, href) => {
    const u = safeUrl(href);
    return u ? `<a href="${u}" target="_blank" rel="noopener noreferrer" title="${u}">${html}</a>` : html;
  };
  const image = (alt, src) => {
    const u = safeUrl(src, true);
    if (!u) return esc(`![${alt}](${src})`);
    return `<a class="md-img" href="${u}" target="_blank" rel="noopener noreferrer">` +
      `<img src="${u}" alt="${esc(alt)}" title="${esc(alt)}" loading="lazy" referrerpolicy="no-referrer"></a>`;
  };

  // ---- inline ----
  const INLINE = new RegExp([
    /\\(?<escp>[\\`*_{}[\]()#+\-.!~|>"'])/.source,
    /(?<cf>`+)(?<code>[^`]|[^`][\s\S]*?[^`])\k<cf>(?!`)/.source,
    /!\[(?<ialt>[^\]]*)\]\(\s*(?<isrc>[^\s)]+)(?:\s+"[^"]*")?\s*\)/.source,
    /\[(?<ltext>[^\]]+)\]\(\s*(?<lhref>[^\s)]+)(?:\s+"[^"]*")?\s*\)/.source,
    /<(?<auto>https?:\/\/[^>\s]+)>/.source,
    /(?<![\w"'=/])(?<bare>https?:\/\/[^\s<>]+?)(?=[.,;:!?'")\]]*(?:\s|$))/.source,
    /\*\*(?=\S)(?<strong>[\s\S]+?)(?<=\S)\*\*/.source,
    /(?<![A-Za-z0-9])__(?=\S)(?<strong2>[\s\S]+?)(?<=\S)__(?![A-Za-z0-9])/.source,
    /\*(?=\S)(?<em>[^*\n]+?)(?<=\S)\*/.source,
    /(?<![A-Za-z0-9])_(?=\S)(?<em2>[^_\n]+?)(?<=\S)_(?![A-Za-z0-9])/.source,
    /~~(?=\S)(?<del>[\s\S]+?)(?<=\S)~~/.source,
  ].join("|"), "g");

  function inline(s) {
    let out = "", last = 0, m;
    INLINE.lastIndex = 0;
    const re = new RegExp(INLINE.source, "g");
    while ((m = re.exec(s))) {
      out += esc(s.slice(last, m.index));
      last = re.lastIndex;
      const g = m.groups;
      if (g.escp !== undefined) out += esc(g.escp);
      else if (g.code !== undefined) out += `<code>${esc(g.code.replace(/^ (.+) $/, "$1"))}</code>`;
      else if (g.ialt !== undefined) out += image(g.ialt, g.isrc);
      else if (g.ltext !== undefined) out += link(inline(g.ltext), g.lhref);
      else if (g.auto) out += link(esc(g.auto), g.auto);
      else if (g.bare) out += link(esc(g.bare), g.bare);
      else if (g.strong !== undefined) out += `<strong>${inline(g.strong)}</strong>`;
      else if (g.strong2 !== undefined) out += `<strong>${inline(g.strong2)}</strong>`;
      else if (g.em !== undefined) out += `<em>${inline(g.em)}</em>`;
      else if (g.em2 !== undefined) out += `<em>${inline(g.em2)}</em>`;
      else if (g.del !== undefined) out += `<del>${inline(g.del)}</del>`;
    }
    return out + esc(s.slice(last));
  }

  // ---- code colouring: comments, strings, numbers and keywords; enough to make a block readable ----
  const KEYWORDS = new Set(("abstract as assert async await break case catch class const continue def default defer del " +
    "do elif else enum except export extends extern final finally fn for from func function go guard if impl import in " +
    "inline interface internal is lambda let local loop match mod module mut namespace new nil none not null of or and " +
    "override package pass private protected pub public raise return select self static struct super switch then this " +
    "throw throws trait true false try type typedef typealias union unsafe use using var void where while with yield " +
    "int float double bool boolean char string str byte long short unsigned void auto echo exit fi esac done elif " +
    "export then until local readonly declare True False None").split(" "));
  const HASH_COMMENTS = new Set(["py", "python", "sh", "bash", "zsh", "shell", "yaml", "yml", "toml", "ruby", "rb", "r",
    "perl", "make", "makefile", "dockerfile", "ini", "conf", "env", ""]);

  function highlight(code, lang) {
    lang = (lang || "").toLowerCase();
    const hash = HASH_COMMENTS.has(lang);
    const re = hash
      ? /(#.*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)/g
      : /(\/\/.*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)/g;
    let out = "", last = 0, m;
    while ((m = re.exec(code))) {
      out += esc(code.slice(last, m.index));
      last = re.lastIndex;
      if (m[1]) out += `<span class="hl-c">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="hl-s">${esc(m[2])}</span>`;
      else if (m[3]) out += `<span class="hl-n">${esc(m[3])}</span>`;
      else out += KEYWORDS.has(m[4]) ? `<span class="hl-k">${m[4]}</span>` : m[4];
    }
    return out + esc(code.slice(last));
  }

  function codeBlock(code, lang) {
    const l = esc(lang);
    return `<div class="md-code"><div class="md-code-head"><span>${l || "code"}</span>` +
      `<button type="button" class="md-copy" title="Copy code">Copy</button></div>` +
      `<pre><code${l ? ` class="language-${l}"` : ""}>${highlight(code, lang)}</code></pre></div>`;
  }

  // ---- blocks ----
  const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/;
  const HEAD = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const QUOTE = /^ {0,3}> ?(.*)$/;
  const LIST = /^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

  const isTableStart = (lines, i) => i + 1 < lines.length && lines[i].includes("|") && TABLE_SEP.test(lines[i + 1]);
  // A list line may interrupt a paragraph only if it is a bullet or starts at 1 (so "2024. a year" stays text).
  const interrupts = (line) => {
    const m = LIST.exec(line);
    return m && (m[4] !== "" || m[3] !== "") && (!/\d/.test(m[2]) || /^1[.)]$/.test(m[2]));
  };
  const blockStart = (line) => FENCE.test(line) || HEAD.test(line) || HR.test(line) || QUOTE.test(line);

  function blocks(lines) {
    let html = "", i = 0, m;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if ((m = FENCE.exec(line))) {
        const ch = m[1][0], n = m[1].length, close = new RegExp(`^ {0,3}\\${ch}{${n},}\\s*$`);
        const buf = []; i++;
        while (i < lines.length && !close.test(lines[i])) buf.push(lines[i++]);
        i++;
        html += codeBlock(buf.join("\n"), m[2]);
        continue;
      }
      if ((m = HEAD.exec(line))) { html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; i++; continue; }
      if (HR.test(line)) { html += "<hr>"; i++; continue; }
      if (QUOTE.test(line)) {
        const buf = [];
        while (i < lines.length && (m = QUOTE.exec(lines[i]))) { buf.push(m[1]); i++; }
        html += `<blockquote>${blocks(buf)}</blockquote>`;
        continue;
      }
      if ((m = LIST.exec(line)) && (m[4] !== "" || m[3] !== "")) { const r = list(lines, i); html += r.html; i = r.next; continue; }
      if (isTableStart(lines, i)) { const r = table(lines, i); if (r) { html += r.html; i = r.next; continue; } }
      const buf = [line]; i++;
      while (i < lines.length && lines[i].trim() && !blockStart(lines[i]) && !interrupts(lines[i]) && !isTableStart(lines, i)) buf.push(lines[i++]);
      html += paragraph(buf.join("\n"));
    }
    return html;
  }

  function paragraph(text) {
    const h = inline(text).replace(/\n/g, "<br>");
    if (/^(?:\s*<a class="md-img"[^>]*><img [^>]*><\/a>\s*(?:<br>)?)+$/.test(h)) return `<div class="md-gallery">${h.replace(/<br>/g, "")}</div>`;
    return `<p>${h}</p>`;
  }

  function list(lines, i) {
    const first = LIST.exec(lines[i]);
    const ordered = /\d/.test(first[2]), indent = first[1].length;
    const items = [];
    let loose = false, cur = null, contentIndent = 0;
    for (; i < lines.length; i++) {
      const line = lines[i], m = LIST.exec(line);
      if (m && m[1].length <= indent && (m[4] !== "" || m[3] !== "")) {
        if (m[1].length < indent || /\d/.test(m[2]) !== ordered) break;   // parent's item, or a different list
        if (m[1].length === indent && cur === null && items.length === 0) { /* first item */ }
        const spaces = m[3].length > 4 ? 1 : m[3].length;
        contentIndent = indent + m[2].length + Math.max(1, spaces);
        cur = { lines: [spaces > 4 ? " ".repeat(m[3].length - 1) + m[4] : m[4]] };
        items.push(cur);
        continue;
      }
      if (!line.trim()) { cur.lines.push(""); continue; }
      const lead = line.match(/^ */)[0].length;
      if (lead >= contentIndent || (m && lead > indent)) { cur.lines.push(line.slice(Math.min(lead, contentIndent))); continue; }
      // Lazy continuation: plain text right after a non-blank item line.
      if (!m && !blockStart(line) && cur.lines.length && cur.lines[cur.lines.length - 1].trim()) { cur.lines.push(line.trim()); continue; }
      break;
    }
    for (const it of items) {
      while (it.lines.length && !it.lines[it.lines.length - 1].trim()) it.lines.pop();
      if (it.lines.some((l) => !l.trim())) loose = true;
    }
    const tag = ordered ? "ol" : "ul";
    const start = ordered && parseInt(first[2], 10) !== 1 ? ` start="${parseInt(first[2], 10)}"` : "";
    let html = `<${tag}${start}>`;
    for (const it of items) {
      let cls = "", pre = "";
      const task = /^\[([ xX])\]\s+/.exec(it.lines[0] || "");
      if (task) { it.lines[0] = it.lines[0].slice(task[0].length); cls = ' class="task"'; pre = `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> `; }
      let inner = blocks(it.lines);
      if (!loose) inner = inner.replace(/^<p>([\s\S]*?)<\/p>/, "$1");
      html += `<li${cls}>${pre}${inner}</li>`;
    }
    return { html: html + `</${tag}>`, next: i };
  }

  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  }

  function table(lines, i) {
    const head = splitRow(lines[i]), seps = splitRow(lines[i + 1]);
    if (head.length !== seps.length) return null;
    const align = seps.map((s) => (/^:-+:$/.test(s) ? "center" : /^-+:$/.test(s) ? "right" : /^:-+$/.test(s) ? "left" : ""));
    const cell = (tag, k, html) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${html}</${tag}>`;
    let html = `<div class="md-table"><table><thead><tr>${head.map((c, k) => cell("th", k, inline(c))).join("")}</tr></thead><tbody>`;
    for (i += 2; i < lines.length && lines[i].trim() && lines[i].includes("|") && !blockStart(lines[i]); i++) {
      const cells = splitRow(lines[i]);
      html += `<tr>${align.map((_, k) => cell("td", k, inline(cells[k] ?? ""))).join("")}</tr>`;
    }
    return { html: html + "</tbody></table></div>", next: i };
  }

  function render(src) {
    const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n")
      .map((l) => l.replace(/^\t+/, (t) => "    ".repeat(t.length)));
    return blocks(lines);
  }

  // Update an element in place: blocks that did not change are kept, so images and selections in earlier
  // paragraphs survive the re-renders of a streaming reply.
  function into(el, src) {
    const t = document.createElement("template");
    t.innerHTML = render(src);
    const fresh = [...t.content.children];
    let k = 0;
    while (k < el.children.length && k < fresh.length && el.children[k].outerHTML === fresh[k].outerHTML) k++;
    while (el.children.length > k) el.lastElementChild.remove();
    for (; k < fresh.length; k++) el.append(fresh[k]);
  }

  return { render, into, esc };
})();
