import SwiftUI

struct ChatView: View {
    @EnvironmentObject var settings: Settings
    @EnvironmentObject var engine: ChatEngine
    @State private var input = ""
    @State private var showHistory = false
    @State private var showSettings = false
    @State private var status: ConnState = .idle
    @State private var follow = true      // keep the newest text in view; dragging the list down turns it off
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                messages
                composer
            }
            .navigationTitle(engine.chat.title.isEmpty ? "New chat" : engine.chat.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showHistory = true } label: { Image(systemName: "clock.arrow.circlepath") }
                        .accessibilityLabel("History")
                }
                ToolbarItem(placement: .principal) { titleView }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { engine.newChat(); input = "" } label: { Image(systemName: "square.and.pencil") }
                        .accessibilityLabel("New chat")
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .sheet(isPresented: $showHistory) { HistoryView(isPresented: $showHistory) }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .task { await checkStatus() }
            .onChange(of: showSettings) { _, open in if !open { Task { await checkStatus() } } }
        }
    }

    private var titleView: some View {
        VStack(spacing: 1) {
            Text(engine.chat.title.isEmpty ? "New chat" : engine.chat.title).font(.headline).lineLimit(1)
            Button { Task { await checkStatus() } } label: {
                HStack(spacing: 4) {
                    Circle().fill(statusColor).frame(width: 6, height: 6)
                    Text(statusText).font(.caption2).foregroundStyle(.secondary)
                }
            }.buttonStyle(.plain)
        }
    }
    private var statusColor: Color {
        switch status { case .ok: return .green; case .failed: return .red; default: return .gray }
    }
    private var statusText: String {
        switch status {
        case .ok(let m): return m
        case .failed: return "Can’t reach Mac · tap to retry"
        case .testing: return "Connecting…"
        case .idle: return "…"
        }
    }

    private func checkStatus() async {
        guard let base = settings.config.baseURL else { return }
        status = .testing
        do { status = .ok(model: try await HarnessAPI(base: base, apiKey: settings.config.apiKey).probe().model) }
        catch { status = .failed((error as? APIError)?.message ?? "") }
    }

    // MARK: messages

    private var messages: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if engine.chat.messages.isEmpty { emptyState }
                    ForEach(engine.chat.messages) { m in
                        MessageView(message: m, live: engine.viewingRunning && m.id == engine.chat.messages.last?.id)
                            .id(m.id)
                    }
                    if let err = engine.visibleError { errorBanner(err) }
                    Color.clear.frame(height: 1).id("bottom").onAppear { follow = true }
                }
                .padding(.horizontal, 14).padding(.top, 10)
            }
            .scrollDismissesKeyboard(.interactively)
            // Follow new text only while you are at the bottom: pulling the list down to read stops it, and
            // scrolling back to the bottom resumes it.
            .simultaneousGesture(DragGesture(minimumDistance: 12).onChanged { v in
                if v.translation.height > 12 { follow = false }
            })
            .onChange(of: engine.chat.messages.last?.content) { _, _ in
                if engine.viewingRunning && follow { proxy.scrollTo("bottom") }
            }
            .onChange(of: engine.chat.messages.last?.reasoning) { _, _ in
                if engine.viewingRunning && follow { proxy.scrollTo("bottom") }
            }
            .onChange(of: engine.chat.messages.count) { _, _ in
                if follow || engine.viewingRunning { withAnimation { proxy.scrollTo("bottom") } }
            }
            .onChange(of: engine.chat.id) { _, _ in follow = true; proxy.scrollTo("bottom") }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "bolt.fill").font(.system(size: 34)).foregroundStyle(.tint)
            Text("Ask your Mac anything").font(.title3.bold())
            Text("Running privately on your own hardware.").font(.subheadline).foregroundStyle(.secondary)
            VStack(spacing: 8) {
                ForEach(["Explain how speculative decoding works", "Write a Python script to rename files by date",
                         "Give me a 3-day itinerary for Lisbon"], id: \.self) { s in
                    Button { input = s; focused = true } label: {
                        Text(s).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }.buttonStyle(.plain)
                }
            }.padding(.top, 6)
        }.frame(maxWidth: .infinity).padding(.top, 40)
    }

    private func errorBanner(_ err: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(err, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            HStack {
                Button("Try again") { engine.retry() }
                Button("Check connection") { showSettings = true }
            }.font(.footnote.weight(.semibold))
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: composer

    private var composer: some View {
        VStack(spacing: 8) {
            if engine.streaming && !engine.viewingRunning {
                Label("Another chat is still replying. You can send here once it finishes.", systemImage: "hourglass")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 8) {
                Menu {
                    Picker("Thinking", selection: $settings.config.effort) {
                        ForEach(Effort.allCases) { e in Text("\(e.label) — \(e.detail)").tag(e) }
                    }
                } label: {
                    chip("brain", "Thinking: " + settings.config.effort.label, on: settings.config.effort != .off)
                }
                Button { settings.config.web.toggle() } label: {
                    chip("globe", "Web", on: settings.config.web)
                }
                Spacer()
            }
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Message", text: $input, axis: .vertical)
                    .lineLimit(1...6).focused($focused)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20))
                    .submitLabel(.send)
                Button(action: sendOrStop) {
                    Image(systemName: engine.viewingRunning ? "stop.circle.fill" : "arrow.up.circle.fill")
                        .font(.system(size: 34))
                        .foregroundStyle(canSend || engine.viewingRunning ? Color.accentColor : Color.gray.opacity(0.4))
                }
                .disabled(!canSend && !engine.viewingRunning)
                .accessibilityLabel(engine.viewingRunning ? "Stop" : "Send")
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(.bar)
    }

    private var canSend: Bool { !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !engine.streaming }

    private func sendOrStop() {
        if engine.viewingRunning { engine.stop(); return }
        follow = true
        engine.send(input)
        input = ""
    }

    private func chip(_ icon: String, _ text: String, on: Bool) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.caption)
            Text(text).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .foregroundStyle(on ? Color.accentColor : .secondary)
        .background(on ? Color.accentColor.opacity(0.14) : Color(.secondarySystemBackground), in: Capsule())
    }
}

struct MessageView: View {
    let message: ChatMessage
    var live: Bool

    var body: some View {
        if message.role == "user" {
            HStack {
                Spacer(minLength: 48)
                Text(message.content)
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .foregroundStyle(.white)
                    .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 18))
                    .textSelection(.enabled)
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                if !message.reasoning.isEmpty { thinking }
                ForEach(message.tools) { t in
                    HStack(spacing: 6) {
                        if t.running { ProgressView().controlSize(.mini) }
                        else { Image(systemName: t.ok ? (t.name == "image_search" ? "photo" : "globe") : "exclamationmark.triangle").font(.caption2) }
                        Text(t.summary).font(.caption).lineLimit(2)
                    }
                    .foregroundStyle(t.ok ? Color.secondary : Color.orange)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Color(.secondarySystemBackground), in: Capsule())
                }
                if !message.content.isEmpty {
                    MarkdownView(text: message.content, streaming: live)
                } else if live && message.reasoning.isEmpty && message.tools.isEmpty {
                    HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Waiting for the Mac…").font(.footnote).foregroundStyle(.secondary) }
                }
                if !message.stats.isEmpty {
                    Text(message.stats).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private var thinking: some View {
        DisclosureGroup {
            Text(message.reasoning).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 4)
        } label: {
            HStack(spacing: 6) {
                if live && message.content.isEmpty { ProgressView().controlSize(.mini) }
                else { Image(systemName: "brain").font(.caption) }
                Text(live && message.content.isEmpty ? "Thinking…" : "Thought for \(message.reasoning.count) chars")
                    .font(.footnote.weight(.medium))
            }.foregroundStyle(.secondary)
        }
        .padding(10)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }
}
