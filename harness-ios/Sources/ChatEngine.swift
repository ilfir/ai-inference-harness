import Foundation

/// Runs a conversation against the backend, including the web tool loop, and keeps the history saved.
@MainActor
final class ChatEngine: ObservableObject {
    @Published var chat = Chat()
    @Published var streaming = false
    @Published var error: String?

    let settings: Settings
    let store: ChatStore
    private var task: Task<Void, Never>?
    private let maxToolRounds = 8

    init(settings: Settings, store: ChatStore) {
        self.settings = settings
        self.store = store
    }

    func newChat() {
        guard !streaming else { return }
        chat = Chat()
        error = nil
    }

    func open(_ c: Chat) {
        guard !streaming else { return }
        chat = c
        error = nil
    }

    func stop() { task?.cancel() }

    func send(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !streaming, !text.isEmpty, settings.config.isConfigured else { return }
        if chat.title.isEmpty { chat.title = String(text.prefix(60)) }
        chat.messages.append(ChatMessage(role: "user", content: text))
        chat.messages.append(ChatMessage(role: "assistant", content: ""))
        store.save(chat)
        streaming = true
        error = nil
        task = Task { await run() }
    }

    /// Retry the last question after an error or a stopped reply.
    func retry() {
        guard !streaming, let last = chat.messages.last(where: { $0.role == "user" }) else { return }
        while let m = chat.messages.last, m.role == "assistant" || m.id == last.id { chat.messages.removeLast() }
        send(last.content)
    }

    private func mutateLast(_ f: (inout ChatMessage) -> Void) {
        guard !chat.messages.isEmpty else { return }
        f(&chat.messages[chat.messages.count - 1])
    }

    private func run() async {
        let cfg = settings.config
        guard let base = cfg.baseURL else { return }
        let api = HarnessAPI(base: base, apiKey: cfg.apiKey)

        var messages: [[String: Any]] = []
        var system = cfg.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if cfg.web { system += (system.isEmpty ? "" : "\n\n") + WebTools.systemHint() }
        if !system.isEmpty { messages.append(["role": "system", "content": system]) }
        for m in chat.messages.dropLast() { messages.append(["role": m.role, "content": m.content]) }

        let started = Date()
        var totalTokens = 0
        var lastTimings: [String: Any]?

        do {
            for round in 0...maxToolRounds {
                let kwargs: [String: Any] = cfg.effort == .off
                    ? ["enable_thinking": false]
                    : ["enable_thinking": true, "reasoning_effort": cfg.effort.rawValue]
                var body: [String: Any] = [
                    "messages": messages, "stream": true,
                    "temperature": cfg.temperature, "top_p": cfg.topP, "top_k": Int(cfg.topK),
                    "chat_template_kwargs": kwargs,
                ]
                if cfg.web { body["tools"] = WebTools.tools }

                // Separate rounds of thinking visually.
                mutateLast { if !$0.reasoning.isEmpty && !$0.reasoning.hasSuffix("\n\n") { $0.reasoning += "\n\n" } }
                let r = try await api.streamRound(body) { [weak self] content, reasoning in
                    self?.mutateLast { $0.content += content; $0.reasoning += reasoning }
                }
                if let t = r.timings {
                    lastTimings = t
                    totalTokens += t["predicted_n"] as? Int ?? 0
                }
                if r.calls.isEmpty || round == maxToolRounds { break }

                var assistant: [String: Any] = ["role": "assistant", "content": r.content,
                    "tool_calls": r.calls.enumerated().map { i, c in
                        ["id": c.id.isEmpty ? "call_\(round)_\(i)" : c.id, "type": "function",
                         "function": ["name": c.name, "arguments": c.arguments]] as [String: Any]
                    }]
                if !r.reasoning.isEmpty { assistant["reasoning_content"] = r.reasoning }
                messages.append(assistant)
                for (i, c) in r.calls.enumerated() {
                    let id = c.id.isEmpty ? "call_\(round)_\(i)" : c.id
                    let label = Self.label(c)
                    let ev = ToolEvent(name: c.name, summary: label, running: true)
                    mutateLast { $0.tools.append(ev) }
                    let (result, summary, ok) = await WebTools.call(name: c.name, arguments: c.arguments)
                    mutateLast { m in
                        if let k = m.tools.firstIndex(where: { $0.id == ev.id }) {
                            m.tools[k].running = false; m.tools[k].ok = ok
                            m.tools[k].summary = label + " → " + summary
                        }
                    }
                    messages.append(["role": "tool", "tool_call_id": id, "content": result])
                    try Task.checkCancellation()
                }
            }
            mutateLast { $0.stats = Self.stats(lastTimings, tokens: totalTokens, elapsed: Date().timeIntervalSince(started)) }
        } catch is CancellationError {
            mutateLast { m in
                m.tools.indices.forEach { m.tools[$0].running = false }
                m.stats = "stopped"
            }
        } catch {
            self.error = (error as? APIError)?.message ?? error.localizedDescription
        }
        // Drop an empty assistant bubble (error before any output).
        if let m = chat.messages.last, m.role == "assistant", m.content.isEmpty, m.reasoning.isEmpty, m.tools.isEmpty {
            chat.messages.removeLast()
        }
        streaming = false
        store.save(chat)
    }

    private static func label(_ c: ToolCall) -> String {
        let args = (try? JSONSerialization.jsonObject(with: Data(c.arguments.utf8)) as? [String: Any]) ?? [:]
        let arg = (args["query"] as? String) ?? (args["url"] as? String) ?? ""
        return c.name == "web_search" ? "Searched “\(arg)”" : "Read \(arg)"
    }

    private static func stats(_ t: [String: Any]?, tokens: Int, elapsed: TimeInterval) -> String {
        var parts: [String] = []
        if tokens > 0 { parts.append("\(tokens) tokens") }
        if let tps = t?["predicted_per_second"] as? Double { parts.append(String(format: "%.1f tok/s", tps)) }
        if let ms = t?["prompt_ms"] as? Double, ms > 0 { parts.append(String(format: "prompt %.1fs", ms / 1000)) }
        parts.append(String(format: "%.1fs total", elapsed))
        return parts.joined(separator: " · ")
    }
}
