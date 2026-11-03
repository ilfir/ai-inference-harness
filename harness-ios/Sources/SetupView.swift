import SwiftUI

enum ConnState: Equatable {
    case idle, testing
    case ok(model: String)
    case failed(String)
}

/// Guided connection screen: shown on first launch and from Settings → Connection.
struct SetupView: View {
    @EnvironmentObject var settings: Settings
    @Environment(\.dismiss) private var dismiss
    var isFirstRun = false

    @State private var state: ConnState = .idle
    @State private var showKey = false

    var body: some View {
        Form {
            if isFirstRun {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Image(systemName: "bolt.horizontal.circle.fill")
                            .font(.system(size: 44)).foregroundStyle(.tint)
                        Text("Connect to your Mac").font(.title2.bold())
                        Text("AI Harness chats with the model running on your inference Mac, over your home Wi‑Fi. Nothing leaves your network.")
                            .foregroundStyle(.secondary)
                    }.padding(.vertical, 4)
                }
            }

            Section {
                step(1, "On the Mac, make sure the backend is running",
                     "Open the AI Harness Mac app → Dashboard → Start. Its status should say “running”.")
                step(2, "Put this iPhone on the same Wi‑Fi",
                     "Turn off VPNs and Private Relay‑style tricks that hide local devices. Cellular won’t reach it.")
                step(3, "Enter the Mac’s address below",
                     "For example 192.168.1.49. In the Mac app: Settings → Connection shows the address (the part after the @).")
                step(4, "Tap Connect and allow Local Network",
                     "iOS asks once whether AI Harness may find devices on your network. Tap Allow.")
            } header: { Text("Setup") }

            Section {
                HStack {
                    Text("Address")
                    TextField("192.168.1.49", text: $settings.config.host)
                        .multilineTextAlignment(.trailing)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .keyboardType(.URL)
                }
                HStack {
                    Text("Port")
                    TextField("8080", text: $settings.config.port)
                        .multilineTextAlignment(.trailing).keyboardType(.numberPad)
                }
                HStack {
                    Text("API key")
                    Group {
                        if showKey { TextField("optional", text: $settings.config.apiKey) }
                        else { SecureField("optional", text: $settings.config.apiKey) }
                    }
                    .multilineTextAlignment(.trailing)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button { showKey.toggle() } label: { Image(systemName: showKey ? "eye.slash" : "eye") }
                        .buttonStyle(.borderless)
                }
            } header: { Text("Server") } footer: {
                Text("Port 8080 is the default. Only fill in the API key if you set one in the Mac app (Settings → API key).")
            }

            Section {
                Button(action: connect) {
                    HStack {
                        if state == .testing { ProgressView().padding(.trailing, 4) }
                        Text(state == .testing ? "Connecting…" : "Connect").bold()
                        Spacer()
                    }
                }
                .disabled(!settings.config.isConfigured || state == .testing)

                switch state {
                case .ok(let model):
                    Label("Connected · \(model)", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    if isFirstRun {
                        Text("You’re all set — the chat opens now.").font(.footnote).foregroundStyle(.secondary)
                    }
                case .failed(let msg):
                    VStack(alignment: .leading, spacing: 8) {
                        Label(msg, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        troubleshooting
                    }
                default: EmptyView()
                }
            }
        }
        .navigationTitle(isFirstRun ? "Welcome" : "Connection")
        .navigationBarTitleDisplayMode(isFirstRun ? .large : .inline)
        .onChange(of: settings.config.host) { _, _ in state = .idle }
    }

    private func step(_ n: Int, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(n)").font(.footnote.bold()).foregroundStyle(.white)
                .frame(width: 22, height: 22).background(Circle().fill(.tint))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.footnote).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 2)
    }

    private var troubleshooting: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Things to check").font(.footnote.bold())
            Group {
                Text("• Local Network is allowed: iPhone Settings → Privacy & Security → Local Network → AI Harness on.")
                Text("• The iPhone is on the same Wi‑Fi as the Mac (not cellular, not a guest network).")
                Text("• The backend is running and the address is right. Try http://ADDRESS:PORT/health in Safari.")
                Text("• The Mac’s listen address is 0.0.0.0 (Mac app → Settings), not 127.0.0.1.")
            }.font(.footnote).foregroundStyle(.secondary)
            Button("Open iPhone Settings") {
                if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
            }.font(.footnote.weight(.semibold))
        }
    }

    private func connect() {
        guard let base = settings.config.baseURL else { return }
        state = .testing
        let api = HarnessAPI(base: base, apiKey: settings.config.apiKey)
        Task {
            do {
                let p = try await api.probe()
                state = .ok(model: p.model)
                try? await Task.sleep(nanoseconds: isFirstRun ? 900_000_000 : 400_000_000)
                settings.config.connected = true   // first run: the root view switches to the chat
                if !isFirstRun { dismiss() }
            } catch {
                state = .failed((error as? APIError)?.message ?? error.localizedDescription)
            }
        }
    }
}
