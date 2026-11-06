import Foundation

struct ToolCall { var id = ""; var name = ""; var arguments = "" }

struct RoundResult {
    var content = ""
    var reasoning = ""
    var calls: [ToolCall] = []
    var timings: [String: Any]?
}

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Talks to llama-server's OpenAI-compatible API on the inference Mac.
struct HarnessAPI {
    let base: URL
    let apiKey: String

    private func request(_ path: String, timeout: TimeInterval = 10) -> URLRequest {
        var r = URLRequest(url: base.appendingPathComponent(path), timeoutInterval: timeout)
        if !apiKey.isEmpty { r.setValue("Bearer " + apiKey, forHTTPHeaderField: "Authorization") }
        return r
    }

    struct Probe { var model: String; var contextSize: Int? }

    /// Checks /health and reads the model name. Throws a human-readable error.
    func probe() async throws -> Probe {
        do {
            let (_, resp) = try await URLSession.shared.data(for: request("health", timeout: 6))
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 { throw APIError(message: "The server wants an API key (or the key is wrong).") }
            if code == 503 { throw APIError(message: "The server is up but the model is still loading. Try again in a minute.") }
            if code != 200 { throw APIError(message: "The server answered with HTTP \(code).") }
        } catch let e as APIError {
            throw e
        } catch {
            throw APIError(message: Self.describe(error))
        }
        var name = "model"
        var ctx: Int?
        if let (data, _) = try? await URLSession.shared.data(for: request("v1/models")),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let arr = obj["data"] as? [[String: Any]], let id = arr.first?["id"] as? String { name = id }
            else if let arr = obj["models"] as? [[String: Any]], let id = arr.first?["name"] as? String { name = id }
            if let arr = obj["data"] as? [[String: Any]], let meta = arr.first?["meta"] as? [String: Any] {
                ctx = meta["n_ctx_train"] as? Int
            }
        }
        return Probe(model: name, contextSize: ctx)
    }

    static func describe(_ error: Error) -> String {
        guard let e = error as? URLError else { return error.localizedDescription }
        switch e.code {
        case .timedOut: return "Timed out. The Mac is not answering on that address and port."
        case .cannotConnectToHost: return "Connection refused. The Mac is reachable but nothing is listening on that port. Is the backend running?"
        case .notConnectedToInternet: return "This iPhone is offline, or Local Network access is turned off for AI Harness."
        case .cannotFindHost, .dnsLookupFailed: return "Cannot find that host name. Try the Mac's IP address instead."
        case .networkConnectionLost: return "The connection dropped."
        case .cancelled: return "Cancelled."
        default: return e.localizedDescription
        }
    }

    /// Streams one chat-completions round. `onDelta` receives new content and reasoning text as it arrives.
    func streamRound(_ body: [String: Any], onDelta: @MainActor (String, String) -> Void) async throws -> RoundResult {
        var r = request("v1/chat/completions", timeout: 1800)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (bytes, resp): (URLSession.AsyncBytes, URLResponse)
        do {
            do { (bytes, resp) = try await URLSession.shared.bytes(for: r) }
            catch let e as URLError where e.code == .networkConnectionLost || e.code == .notConnectedToInternet {
                // Stale keep-alive connection or the app was just suspended: one fresh attempt.
                try await Task.sleep(nanoseconds: 500_000_000)
                (bytes, resp) = try await URLSession.shared.bytes(for: r)
            }
        } catch is CancellationError { throw CancellationError() }
        catch { throw APIError(message: Self.describe(error)) }
        if let h = resp as? HTTPURLResponse, h.statusCode != 200 {
            var text = ""
            for try await line in bytes.lines { text += line; if text.count > 300 { break } }
            throw APIError(message: "Server error \(h.statusCode): \(text.prefix(300))")
        }
        var res = RoundResult()
        var calls: [Int: ToolCall] = [:]
        do {
            for try await line in bytes.lines {
                guard line.hasPrefix("data:") else { continue }
                let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                if payload == "[DONE]" { continue }
                guard let data = payload.data(using: .utf8),
                      let chunk = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                if let err = chunk["error"] as? [String: Any] {
                    throw APIError(message: (err["message"] as? String) ?? "Server error")
                }
                if let t = chunk["timings"] as? [String: Any] { res.timings = t }
                guard let choice = (chunk["choices"] as? [[String: Any]])?.first,
                      let delta = choice["delta"] as? [String: Any] else { continue }
                let c = delta["content"] as? String ?? ""
                let rs = delta["reasoning_content"] as? String ?? ""
                res.content += c
                res.reasoning += rs
                for tc in delta["tool_calls"] as? [[String: Any]] ?? [] {
                    let idx = tc["index"] as? Int ?? 0
                    var call = calls[idx] ?? ToolCall()
                    if let id = tc["id"] as? String, !id.isEmpty { call.id = id }
                    if let fn = tc["function"] as? [String: Any] {
                        call.name += fn["name"] as? String ?? ""
                        call.arguments += fn["arguments"] as? String ?? ""
                    }
                    calls[idx] = call
                }
                if !c.isEmpty || !rs.isEmpty { await onDelta(c, rs) }
            }
        } catch let e as APIError {
            throw e
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            if (error as? URLError)?.code == .cancelled { throw CancellationError() }
            throw APIError(message: Self.describe(error))
        }
        res.calls = calls.keys.sorted().map { calls[$0]! }
        return res
    }
}
