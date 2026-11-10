import SwiftUI
import UIKit

// Markdown for chat replies, the iPhone counterpart of the Mac app's markdown.js: headings, paragraphs, emphasis,
// inline code, fenced code (language label, copy button, light syntax colouring), block quotes, nested ordered /
// unordered / task lists, tables, rules, links, bare URLs and inline images.
// Only http(s) links and images are honoured, so model output or fetched web text cannot open other schemes.

indirect enum MDBlock {
    case heading(Int, String)
    case paragraph(String)
    case image(alt: String, url: URL, link: URL?)
    case code(lang: String, text: String)
    case quote([MDBlock])
    case list(ordered: Bool, start: Int, items: [MDItem])
    case table(head: [String], align: [Int], rows: [[String]])   // align: 0 leading, 1 center, 2 trailing
    case rule
}

struct MDItem {
    var task: Bool?          // nil: plain item, false / true: unchecked / checked task
    var blocks: [MDBlock]
}

// MARK: - Parser

enum MDParser {
    private static func rx(_ p: String) -> NSRegularExpression { try! NSRegularExpression(pattern: p) }
    private static let fence = rx(#"^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)"#)
    private static let heading = rx(#"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$"#)
    private static let rule = rx(#"^ {0,3}([-*_])(?:\s*\1){2,}\s*$"#)
    private static let quote = rx(#"^ {0,3}> ?(.*)$"#)
    private static let list = rx(#"^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$"#)
    private static let tableSep = rx(#"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$"#)
    private static let task = rx(#"^\[([ xX])\]\s+(.*)$"#)
    private static let images = rx(#"\[!\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+"[^"]*")?\s*\)\]\(\s*([^\s)]+)(?:\s+"[^"]*")?\s*\)|!\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+"[^"]*")?\s*\)"#)
    private static let partialImage = rx(#"!(?:\[[^\]\n]*|\[[^\]\n]*\]\(?[^)\n]*)$"#)

    static func parse(_ src: String) -> [MDBlock] {
        let lines = src.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\t", with: "    ")
            .components(separatedBy: "\n")
        return blocks(lines)
    }

    /// While a reply streams, hide an image whose Markdown has not finished arriving instead of showing raw "![alt](ht".
    static func trimPartialImage(_ s: String) -> String {
        let ns = s as NSString
        guard let m = partialImage.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return s }
        return ns.substring(to: m.range.location)
    }

    static func safeURL(_ s: String) -> URL? {
        var t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("<") && t.hasSuffix(">") { t = String(t.dropFirst().dropLast()) }
        guard let u = URL(string: t), let scheme = u.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = u.host, !host.isEmpty else { return nil }
        return u
    }

    private static func blank(_ s: String) -> Bool { s.trimmingCharacters(in: .whitespaces).isEmpty }
    private static func isBlockStart(_ l: String) -> Bool {
        fence.groups(l) != nil || heading.groups(l) != nil || rule.groups(l) != nil || quote.groups(l) != nil
    }
    private static func isItem(_ g: [String]) -> Bool { !g[4].isEmpty || !g[3].isEmpty }
    /// A list may interrupt a paragraph only if it is a bullet or starts at 1, so "2024. a year" stays text.
    private static func interrupts(_ l: String) -> Bool {
        guard let g = list.groups(l), isItem(g) else { return false }
        return !g[2].first!.isNumber || g[2] == "1." || g[2] == "1)"
    }
    private static func isTableStart(_ lines: [String], _ i: Int) -> Bool {
        i + 1 < lines.count && lines[i].contains("|") && tableSep.groups(lines[i + 1]) != nil
    }

    private static func blocks(_ lines: [String]) -> [MDBlock] {
        var out: [MDBlock] = []
        var i = 0
        while i < lines.count {
            let line = lines[i]
            if blank(line) { i += 1; continue }
            if let g = fence.groups(line) {
                let ch = g[1].first!, n = g[1].count
                var buf: [String] = []
                i += 1
                while i < lines.count, !closesFence(lines[i], ch, n) { buf.append(lines[i]); i += 1 }
                i += 1
                out.append(.code(lang: g[2], text: buf.joined(separator: "\n")))
                continue
            }
            if let g = heading.groups(line) { out.append(.heading(g[1].count, g[2])); i += 1; continue }
            if rule.groups(line) != nil { out.append(.rule); i += 1; continue }
            if quote.groups(line) != nil {
                var buf: [String] = []
                while i < lines.count, let g = quote.groups(lines[i]) { buf.append(g[1]); i += 1 }
                out.append(.quote(blocks(buf)))
                continue
            }
            if let g = list.groups(line), isItem(g) {
                let (b, next) = parseList(lines, i)
                out.append(b); i = next
                continue
            }
            if isTableStart(lines, i), let (t, next) = parseTable(lines, i) { out.append(t); i = next; continue }
            var buf = [line]
            i += 1
            while i < lines.count, !blank(lines[i]), !isBlockStart(lines[i]), !interrupts(lines[i]), !isTableStart(lines, i) {
                buf.append(lines[i]); i += 1
            }
            out.append(contentsOf: paragraph(buf.joined(separator: "\n")))
        }
        return out
    }

    private static func closesFence(_ l: String, _ ch: Character, _ n: Int) -> Bool {
        let t = l.trimmingCharacters(in: .whitespaces)
        return t.count >= n && t.allSatisfy { $0 == ch }
    }

    /// Splits a paragraph around images so they can be drawn as pictures between the text.
    private static func paragraph(_ text: String) -> [MDBlock] {
        let ns = text as NSString
        var out: [MDBlock] = []
        var last = 0
        func flush(_ upTo: Int) {
            let t = ns.substring(with: NSRange(location: last, length: upTo - last)).trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { out.append(.paragraph(t)) }
        }
        for m in images.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            func g(_ k: Int) -> String { m.range(at: k).location == NSNotFound ? "" : ns.substring(with: m.range(at: k)) }
            let linked = m.range(at: 1).location != NSNotFound
            let alt = linked ? g(1) : g(4), src = linked ? g(2) : g(5)
            guard let url = safeURL(src) else { continue }
            flush(m.range.location)
            out.append(.image(alt: alt, url: url, link: linked ? safeURL(g(3)) : nil))
            last = m.range.location + m.range.length
        }
        flush(ns.length)
        return out
    }

    private static func parseList(_ lines: [String], _ start: Int) -> (MDBlock, Int) {
        let first = list.groups(lines[start])!
        let ordered = first[2].first!.isNumber
        let indent = first[1].count
        var items: [[String]] = []
        var contentIndent = 0
        var i = start
        while i < lines.count {
            let line = lines[i]
            let m = list.groups(line)
            if let m, isItem(m), m[1].count <= indent {
                if m[1].count < indent || m[2].first!.isNumber != ordered { break }
                let spaces = m[3].count > 4 ? 1 : max(1, m[3].count)
                contentIndent = indent + m[2].count + spaces
                items.append([m[3].count > 4 ? String(repeating: " ", count: m[3].count - 1) + m[4] : m[4]])
                i += 1; continue
            }
            if blank(line) { items[items.count - 1].append(""); i += 1; continue }
            let lead = line.prefix(while: { $0 == " " }).count
            if lead >= contentIndent || (m != nil && lead > indent) {
                items[items.count - 1].append(String(line.dropFirst(min(lead, contentIndent)))); i += 1; continue
            }
            // Lazy continuation: plain text right after a non-blank item line.
            if m == nil, !isBlockStart(line), let l = items[items.count - 1].last, !blank(l) {
                items[items.count - 1].append(line.trimmingCharacters(in: .whitespaces)); i += 1; continue
            }
            break
        }
        let parsed: [MDItem] = items.map { raw in
            var ls = raw
            while let l = ls.last, blank(l) { ls.removeLast() }
            var t: Bool?
            if let first = ls.first, let g = task.groups(first) { t = g[1] != " "; ls[0] = g[2] }
            return MDItem(task: t, blocks: blocks(ls))
        }
        let startNo = ordered ? Int(first[2].dropLast()) ?? 1 : 1
        return (.list(ordered: ordered, start: startNo, items: parsed), i)
    }

    private static func splitRow(_ line: String) -> [String] {
        var s = line.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") && !s.hasSuffix("\\|") { s.removeLast() }
        var cells: [String] = [], cur = ""
        var esc = false
        for c in s {
            if esc { cur += c == "|" ? "|" : "\\" + String(c); esc = false; continue }
            if c == "\\" { esc = true; continue }
            if c == "|" { cells.append(cur.trimmingCharacters(in: .whitespaces)); cur = ""; continue }
            cur.append(c)
        }
        cells.append(cur.trimmingCharacters(in: .whitespaces))
        return cells
    }

    private static func parseTable(_ lines: [String], _ start: Int) -> (MDBlock, Int)? {
        let head = splitRow(lines[start]), seps = splitRow(lines[start + 1])
        guard head.count == seps.count else { return nil }
        let align = seps.map { s -> Int in
            if s.hasPrefix(":") && s.hasSuffix(":") { return 1 }
            return s.hasSuffix(":") ? 2 : 0
        }
        var rows: [[String]] = []
        var i = start + 2
        while i < lines.count, !blank(lines[i]), lines[i].contains("|"), !isBlockStart(lines[i]) {
            var cells = splitRow(lines[i])
            while cells.count < head.count { cells.append("") }
            rows.append(Array(cells.prefix(head.count)))
            i += 1
        }
        return (.table(head: head, align: align, rows: rows), i)
    }
}

extension NSRegularExpression {
    /// Capture groups of the first match (index 0 is the whole match, unmatched groups are ""), or nil.
    func groups(_ s: String) -> [String]? {
        let ns = s as NSString
        guard let m = firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return (0..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? "" : ns.substring(with: m.range(at: $0)) }
    }
}

// MARK: - Inline text

enum MDInline {
    private static let bare = try! NSRegularExpression(pattern: #"(?<![\w"'=/(\[<])https?://[^\s<>]+?(?=[.,;:!?'")\]]*(?:\s|$))"#)
    private static let angle = try! NSRegularExpression(pattern: #"<(https?://[^>\s]+)>"#)

    /// Turns <url> and bare URLs into Markdown links, leaving code spans alone.
    private static func autolink(_ s: String) -> String {
        var parts = s.components(separatedBy: "`")
        for k in stride(from: 0, to: parts.count, by: 2) {
            var p = angle.stringByReplacingMatches(in: parts[k], range: NSRange(location: 0, length: (parts[k] as NSString).length), withTemplate: "[$1]($1)")
            let ns = p as NSString
            var out = "", last = 0
            for m in bare.matches(in: p, range: NSRange(location: 0, length: ns.length)) {
                let url = ns.substring(with: m.range)
                out += ns.substring(with: NSRange(location: last, length: m.range.location - last)) + "[\(url)](\(url))"
                last = m.range.location + m.range.length
            }
            p = out + ns.substring(from: last)
            parts[k] = p
        }
        return parts.joined(separator: "`")
    }

    static func attributed(_ s: String) -> AttributedString {
        var a = (try? AttributedString(markdown: autolink(s), options: .init(allowsExtendedAttributes: true,
                    interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
        var bad: [Range<AttributedString.Index>] = [], code: [Range<AttributedString.Index>] = []
        for run in a.runs {
            if let u = run.link, !["http", "https", "mailto"].contains(u.scheme?.lowercased() ?? "") { bad.append(run.range) }
            if run.inlinePresentationIntent?.contains(.code) == true { code.append(run.range) }
        }
        for r in bad { a[r].link = nil }
        for r in code {
            a[r].font = .system(.callout, design: .monospaced)
            a[r].backgroundColor = Color(.tertiarySystemFill)
        }
        return a
    }
}

// MARK: - Views

struct MarkdownView: View {
    let text: String
    var streaming = false

    var body: some View {
        MDBlocks(blocks: MDParser.parse(streaming ? MDParser.trimPartialImage(text) : text))
            .textSelection(.enabled)
    }
}

struct MDBlocks: View {
    let blocks: [MDBlock]
    var spacing: CGFloat = 10

    var body: some View {
        VStack(alignment: .leading, spacing: spacing) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, b in MDBlockView(block: b) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct MDBlockView: View {
    let block: MDBlock

    var body: some View {
        switch block {
        case .heading(let level, let text):
            Text(MDInline.attributed(text))
                .font(level == 1 ? .title2.bold() : level == 2 ? .title3.bold() : level == 3 ? .headline : .subheadline.bold())
                .padding(.top, 4)
        case .paragraph(let text):
            Text(MDInline.attributed(text))
        case .image(let alt, let url, let link):
            MDImage(alt: alt, url: url, link: link)
        case .code(let lang, let text):
            MDCode(lang: lang, code: text)
        case .quote(let inner):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 2).fill(Color.accentColor).frame(width: 3)
                MDBlocks(blocks: inner, spacing: 6).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .list(let ordered, let start, let items):
            VStack(alignment: .leading, spacing: 5) {
                ForEach(Array(items.enumerated()), id: \.offset) { k, item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        if let done = item.task {
                            Image(systemName: done ? "checkmark.square.fill" : "square").foregroundStyle(done ? Color.accentColor : .secondary)
                        } else {
                            Text(ordered ? "\(start + k)." : "•").foregroundStyle(.secondary).monospacedDigit()
                        }
                        MDBlocks(blocks: item.blocks, spacing: 6)
                    }
                }
            }
        case .table(let head, let align, let rows):
            MDTable(head: head, align: align, rows: rows)
        case .rule:
            Divider().padding(.vertical, 4)
        }
    }
}

struct MDImage: View {
    let alt: String
    let url: URL
    let link: URL?

    var body: some View {
        Link(destination: link ?? url) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let img):
                    img.resizable().scaledToFit().frame(maxHeight: 340).clipShape(RoundedRectangle(cornerRadius: 10))
                case .failure:
                    Label("Image unavailable" + (alt.isEmpty ? "" : ": " + alt), systemImage: "photo.badge.exclamationmark")
                        .font(.footnote).foregroundStyle(.secondary).padding(10)
                        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
                default:
                    RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)).frame(height: 140)
                        .overlay(ProgressView())
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityLabel(alt.isEmpty ? "Image" : alt)
    }
}

struct MDTable: View {
    let head: [String]
    let align: [Int]
    let rows: [[String]]

    private func cell(_ text: String, _ k: Int, header: Bool) -> some View {
        let a: Alignment = align[k] == 1 ? .center : align[k] == 2 ? .trailing : .leading
        return Text(MDInline.attributed(text))
            .font(.footnote.weight(header ? .semibold : .regular))
            .padding(.horizontal, 8).padding(.vertical, 5)
            .frame(minWidth: 56, maxWidth: 240, alignment: a)
            .background(header ? Color(.secondarySystemBackground) : Color.clear)
            .overlay(Rectangle().stroke(Color(.separator), lineWidth: 0.5))
    }

    var body: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow { ForEach(head.indices, id: \.self) { k in cell(head[k], k, header: true) } }
                ForEach(rows.indices, id: \.self) { r in
                    GridRow { ForEach(head.indices, id: \.self) { k in cell(rows[r][k], k, header: false) } }
                }
            }
        }
        .scrollIndicators(.hidden)
    }
}

struct MDCode: View {
    let lang: String
    let code: String
    @State private var copied = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(lang.isEmpty ? "code" : lang).font(.caption2).foregroundStyle(.secondary)
                Spacer()
                Button {
                    UIPasteboard.general.string = code
                    copied = true
                    Task { try? await Task.sleep(nanoseconds: 1_500_000_000); copied = false }
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc").font(.caption2)
                }
                .buttonStyle(.plain).foregroundStyle(copied ? Color.accentColor : .secondary)
            }
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(Color(.tertiarySystemFill))
            ScrollView(.horizontal) {
                Text(MDHighlight.attributed(code, lang: lang))
                    .font(.system(size: 12.5, design: .monospaced))
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: true, vertical: false)
            }
            .scrollIndicators(.hidden)
        }
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color(.separator), lineWidth: 0.5))
    }
}

/// Light syntax colouring: comments, strings, numbers and keywords; enough to make a block readable.
enum MDHighlight {
    private static let keywords: Set<String> = Set(("abstract as assert async await break case catch class const continue def default defer del " +
        "do elif else enum except export extends extern final finally fn for from func function go guard if impl import in " +
        "inline interface internal is lambda let local loop match mod module mut namespace new nil none not null of or and " +
        "override package pass private protected pub public raise return select self static struct super switch then this " +
        "throw throws trait true false try type typedef typealias union unsafe use using var void where while with yield " +
        "int float double bool boolean char string str byte long short unsigned auto echo exit fi esac done until readonly " +
        "declare True False None").split(separator: " ").map(String.init))
    private static let hashLangs: Set<String> = ["py", "python", "sh", "bash", "zsh", "shell", "yaml", "yml", "toml", "ruby", "rb",
                                                  "r", "perl", "make", "makefile", "dockerfile", "ini", "conf", "env", ""]
    private static let hashRx = try! NSRegularExpression(pattern: #"(#.*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)"#)
    private static let cRx = try! NSRegularExpression(pattern: #"(//.*|/\*[\s\S]*?\*/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)"#)

    static func attributed(_ code: String, lang: String) -> AttributedString {
        let re = hashLangs.contains(lang.lowercased()) ? hashRx : cRx
        let ns = code as NSString
        var out = AttributedString()
        var last = 0
        func plain(_ s: String) { out += AttributedString(s) }
        func colored(_ s: String, _ c: Color, bold: Bool = false) {
            var a = AttributedString(s)
            a.foregroundColor = c
            if bold { a.inlinePresentationIntent = .stronglyEmphasized }
            out += a
        }
        for m in re.matches(in: code, range: NSRange(location: 0, length: ns.length)) {
            plain(ns.substring(with: NSRange(location: last, length: m.range.location - last)))
            last = m.range.location + m.range.length
            let text = ns.substring(with: m.range)
            if m.range(at: 1).location != NSNotFound { colored(text, .secondary) }
            else if m.range(at: 2).location != NSNotFound { colored(text, Color(.systemGreen)) }
            else if m.range(at: 3).location != NSNotFound { colored(text, Color(.systemOrange)) }
            else if keywords.contains(text) { colored(text, Color(.systemPurple), bold: true) }
            else { plain(text) }
        }
        plain(ns.substring(from: last))
        return out
    }
}
