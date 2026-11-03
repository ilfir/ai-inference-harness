import Foundation

struct ToolEvent: Codable, Identifiable, Hashable {
    var id = UUID()
    var name: String
    var summary: String
    var ok: Bool = true
    var running: Bool = false
}

struct ChatMessage: Codable, Identifiable, Hashable {
    var id = UUID()
    var role: String            // "user" | "assistant"
    var content: String
    var reasoning: String = ""
    var tools: [ToolEvent] = []
    var stats: String = ""
}

struct Chat: Codable, Identifiable, Hashable {
    var id = UUID()
    var title = ""
    var updated = Date()
    var messages: [ChatMessage] = []
}

enum Effort: String, CaseIterable, Codable, Identifiable {
    case off, low, medium, xhigh
    var id: String { rawValue }
    var label: String {
        switch self {
        case .off: return "Off"
        case .low: return "Low"
        case .medium: return "Medium"
        case .xhigh: return "High"
        }
    }
    var detail: String {
        switch self {
        case .off: return "No thinking. Fastest."
        case .low: return "Brief thinking."
        case .medium: return "Balanced."
        case .xhigh: return "Deep thinking. Slowest."
        }
    }
}

struct Config: Codable, Equatable {
    var host = ""
    var connected = false   // set after the first successful connection test
    var port = "8080"
    var apiKey = ""
    var effort: Effort = .xhigh
    var web = false
    var temperature = 1.0
    var topP = 0.95
    var topK = 20.0
    var systemPrompt = ""

    /// "192.168.1.49" -> http://192.168.1.49:8080 ; a full URL is used as typed.
    var baseURL: URL? {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        if h.isEmpty { return nil }
        if !h.contains("://") { h = "http://" + h + (h.contains(":") ? "" : ":" + port) }
        while h.hasSuffix("/") { h.removeLast() }
        return URL(string: h)
    }
    var isConfigured: Bool { baseURL != nil }
}

@MainActor
final class Settings: ObservableObject {
    @Published var config: Config { didSet { save() } }
    private let key = "config.v1"

    init() {
        if let d = UserDefaults.standard.data(forKey: key), let c = try? JSONDecoder().decode(Config.self, from: d) {
            config = c
        } else {
            config = Config()
        }
    }
    private func save() {
        if let d = try? JSONEncoder().encode(config) { UserDefaults.standard.set(d, forKey: key) }
    }
}

/// Chat history: most recent first, at most 20, kept in the app's Documents folder.
@MainActor
final class ChatStore: ObservableObject {
    static let maxChats = 20
    @Published private(set) var chats: [Chat] = []
    private let file: URL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("chats.json")

    init() {
        if let d = try? Data(contentsOf: file), let c = try? JSONDecoder().decode([Chat].self, from: d) {
            chats = c
        }
    }
    func save(_ chat: Chat) {
        var c = chat
        c.updated = Date()
        chats.removeAll { $0.id == c.id }
        chats.insert(c, at: 0)
        if chats.count > Self.maxChats { chats = Array(chats.prefix(Self.maxChats)) }
        write()
    }
    func delete(_ id: UUID) {
        chats.removeAll { $0.id == id }
        write()
    }
    private func write() {
        if let d = try? JSONEncoder().encode(chats) { try? d.write(to: file, options: .atomic) }
    }
}
