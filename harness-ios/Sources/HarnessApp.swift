import SwiftUI

@main
struct HarnessApp: App {
    @StateObject private var settings: Settings
    @StateObject private var store: ChatStore
    @StateObject private var engine: ChatEngine

    init() {
        let s = Settings(), st = ChatStore()
        _settings = StateObject(wrappedValue: s)
        _store = StateObject(wrappedValue: st)
        _engine = StateObject(wrappedValue: ChatEngine(settings: s, store: st))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(settings)
                .environmentObject(store)
                .environmentObject(engine)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var settings: Settings

    var body: some View {
        Group {
            if settings.config.connected {
                ChatView()
            } else {
                NavigationStack { SetupView(isFirstRun: true) }
            }
        }
    }
}
