import Foundation
import UIKit

/// Runs a conversation against the backend, including the web tool loop, and keeps the history saved.
@MainActor
final class ChatEngine: ObservableObject {
    @Published var chat = Chat()              // what the screen shows
    @Published var streaming = false          // a reply is being generated, in any chat
    @Published var error: String?
    @Published private(set) var runningID: UUID?
    private var errorChatID: UUID?
    /// The chat being generated. It keeps updating while you look at another chat; opening it again shows this copy.
    private var runChat = Chat()

    /// True when the reply being generated belongs to the chat on screen.
    var viewingRunning: Bool { streaming && runningID == chat.id }
    /// The error banner only shows in the chat it happened in.
    var visibleError: String? { errorChatID == chat.id ? error : nil }

    let settings: Settings
    let store: ChatStore
    private var task: Task<Void, Never>?
    private let maxToolRounds = 8

    init(settings: Settings, store: ChatStore) {
        self.settings = settings
        self.store = store
    }

    func newChat() {
        chat = Chat()
        if errorChatID != nil { error = nil; errorChatID = nil }
    }

    func open(_ c: Chat) {
        chat = streaming && c.id == runChat.id ? runChat : c
        if errorChatID != c.id { error = nil; errorChatID = nil }
    }

    func stop() { task?.cancel() }

    func send(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !streaming, !text.isEmpty, settings.config.isConfigured else { return }
        if chat.title.isEmpty { chat.title = String(text.prefix(60)) }
        chat.messages.append(ChatMessage(role: "user", content: text))
        chat.messages.append(ChatMessage(role: "assistant", content: ""))
        store.save(chat)
        runChat = chat
        runningID = chat.id
        streaming = true
        error = nil
        errorChatID = nil
        task = Task { await run() }
    }

    /// Retry the last question after an error or a stopped reply.
    func retry() {
        guard !streaming, let last = chat.messages.last(where: { $0.role == "user" }) else { return }
        while let m = chat.messages.last, m.role == "assistant" || m.id == last.id { chat.messages.removeLast() }
        send(last.content)
    }

    private func mutateLast(_ f: (inout ChatMessage) -> Void) {
        guard !runChat.messages.isEmpty else { return }
        f(&runChat.messages[runChat.messages.count - 1])
        if chat.id == runChat.id { chat = runChat }
    }

    private func run() async {
        // A long web-research reply takes minutes. Keep the screen from auto-locking and ask iOS for extra
        // background time, otherwise iOS suspends the app and the connection to the Mac drops mid-reply.
        UIApplication.shared.isIdleTimerDisabled = true
        var bg = UIBackgroundTaskIdentifier.invalid
        bg = UIApplication.shared.beginBackgroundTask(withName: "chat") {
            UIApplication.shared.endBackgroundTask(bg); bg = .invalid
        }
        defer {
            UIApplication.shared.isIdleTimerDisabled = false
            if bg != .invalid { UIApplication.shared.endBackgroundTask(bg) }
        }
        let cfg = settings.config
        guard let base = cfg.baseURL else { return }
        let api = HarnessAPI(base: base, apiKey: cfg.apiKey)

        var messages: [[String: Any]] = []
        var system = cfg.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if cfg.web { system += (system.isEmpty ? "" : "\n\n") + WebTools.systemHint() }
        if !system.isEmpty { messages.append(["role": "system", "content": system]) }
        for m in runChat.messages.dropLast() { messages.append(["role": m.role, "content": m.content]) }

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
                // On the last round take the tools away so the model has to write its answer from what it gathered;
                // otherwise a model that keeps searching ends the turn with no answer at all.
                let finalRound = round == maxToolRounds
                if cfg.web && !finalRound { body["tools"] = WebTools.tools }
                else if finalRound {
                    body["messages"] = messages + [["role": "user", "content": "You have reached the limit for web lookups. Using what you found so far, write the final answer now. Do not call any tools; say what you could not confirm."]]
                }

                // Separate rounds visually (thinking, and the short preamble the model writes before tool calls).
                mutateLast { if !$0.content.isEmpty && !$0.content.hasSuffix("\n\n") { $0.content += "\n\n" } }
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
            self.errorChatID = runChat.id
        }
        // Drop an empty assistant bubble (error before any output).
        if let m = runChat.messages.last, m.role == "assistant", m.content.isEmpty, m.reasoning.isEmpty, m.tools.isEmpty {
            runChat.messages.removeLast()
        }
        if chat.id == runChat.id { chat = runChat }
        streaming = false
        runningID = nil
        store.save(runChat)
    }

    private static func label(_ c: ToolCall) -> String {
        let args = (try? JSONSerialization.jsonObject(with: Data(c.arguments.utf8)) as? [String: Any]) ?? [:]
        let arg = (args["query"] as? String) ?? (args["url"] as? String) ?? ""
        switch c.name {
        case "web_search": return "Searched “\(arg)”"
        case "image_search": return "Images for “\(arg)”"
        default: return "Read \(arg)"
        }
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
