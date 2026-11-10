import SwiftUI

struct HistoryView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var engine: ChatEngine
    @Binding var isPresented: Bool

    var body: some View {
        NavigationStack {
            Group {
                if store.chats.isEmpty {
                    ContentUnavailableView("No chats yet", systemImage: "bubble.left.and.bubble.right",
                                           description: Text("Your conversations are saved here, on this iPhone."))
                } else {
                    List {
                        ForEach(store.chats) { c in
                            Button {
                                engine.open(c); isPresented = false
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(c.title.isEmpty ? "Untitled" : c.title).lineLimit(2).foregroundStyle(.primary)
                                    Text((engine.runningID == c.id ? "Replying… · " : "") + c.updated.formatted(.relative(presentation: .named)))
                                        .font(.caption).foregroundStyle(engine.runningID == c.id ? Color.green : .secondary)
                                }
                            }
                            .listRowBackground(c.id == engine.chat.id ? Color.accentColor.opacity(0.12) : nil)
                        }
                        .onDelete { idx in
                            for i in idx {
                                let c = store.chats[i]
                                if engine.runningID == c.id { continue }     // stop the reply first
                                store.delete(c.id)
                                if c.id == engine.chat.id { engine.newChat() }
                            }
                        }
                    }
                }
            }
            .navigationTitle("History")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { isPresented = false } }
                ToolbarItem(placement: .bottomBar) {
                    Text("Swipe left to delete · last \(ChatStore.maxChats) chats kept")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
