import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var settings: Settings
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Connection") {
                    NavigationLink {
                        SetupView()
                    } label: {
                        HStack {
                            Text("Mac")
                            Spacer()
                            Text(settings.config.host.isEmpty ? "Not set" : settings.config.host + ":" + settings.config.port)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section {
                    Picker("Thinking", selection: $settings.config.effort) {
                        ForEach(Effort.allCases) { Text($0.label).tag($0) }
                    }
                    Toggle("Web access", isOn: $settings.config.web)
                } header: { Text("Answers") } footer: {
                    Text("Deeper thinking gives better answers but takes longer. Web access lets the model search and read pages (done from this iPhone).")
                }

                Section("Sampling") {
                    slider("Temperature", $settings.config.temperature, 0...2, step: 0.05, format: "%.2f")
                    slider("Top P", $settings.config.topP, 0...1, step: 0.01, format: "%.2f")
                    slider("Top K", $settings.config.topK, 0...100, step: 1, format: "%.0f")
                    Button("Reset to recommended") {
                        settings.config.temperature = 1.0; settings.config.topP = 0.95; settings.config.topK = 20
                    }
                }

                Section {
                    TextField("e.g. Answer briefly.", text: $settings.config.systemPrompt, axis: .vertical)
                        .lineLimit(2...8)
                } header: { Text("System prompt") }

                Section {
                    Button("Forget this Mac and run setup again", role: .destructive) {
                        settings.config.connected = false
                        dismiss()
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    private func slider(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>,
                        step: Double, format: String) -> some View {
        VStack(alignment: .leading) {
            HStack {
                Text(title); Spacer()
                Text(String(format: format, value.wrappedValue)).foregroundStyle(.secondary).monospacedDigit()
            }
            Slider(value: value, in: range, step: step)
        }
    }
}
