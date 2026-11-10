# harness-ios — AI Harness for iPhone

A native SwiftUI chat client for the inference backend, the iPhone counterpart of the Mac app's Chat tab. It talks
straight to the backend's OpenAI-compatible API over your Wi‑Fi (no SSH, no server in between).

- Streaming chat with thinking shown in a collapsible block
- Replies render as Markdown: headings, lists, tables, code blocks with a copy button, block quotes, tappable links and inline images
- Web access also has an image search tool, so "show me a picture of …" works with the Web chip on
- You can switch chats or start a new one while a reply streams; it keeps going and is there when you come back
- The list only follows new text while you are at the bottom, so you can scroll up and read mid-reply
- Thinking effort (Off / Low / Medium / High), web access (search + read pages, run on the phone), sampling, system prompt
- Chat history kept on the phone (last 20), with swipe to delete
- Per-reply stats: tokens, tok/s, prompt time
- Guided first-run setup with connection test and troubleshooting hints

## Install on your iPhone

```bash
harness-ios/install.sh            # builds, signs with your Apple ID and installs on the connected iPhone
```

Needs Xcode, `brew install xcodegen`, and your Apple ID signed in under Xcode → Settings → Accounts. Set
`AIH_TEAM=<team id>` if you are not using the default team in `install.sh`.

One-time steps on the iPhone:

1. **Plug it in** with a cable, unlock it and tap **Trust This Computer**.
2. **Settings → Privacy & Security → Developer Mode → on.** The phone restarts; confirm when asked.
3. After the first install: **Settings → General → VPN & Device Management → your Apple ID → Trust**, then open the app.
4. In the app, enter the Mac's address and tap **Connect**. Tap **Allow** on the Local Network prompt.

Apps signed with a free Apple ID expire after 7 days (`install.sh` prints the exact expiry). Run it again before then
to renew; chat history is kept. A paid Apple Developer Program membership signs for a year, which is the only way to
go longer: put its team ID in `AIH_TEAM` and run `install.sh` once.
After the first install the phone can also be reached over Wi‑Fi (Xcode → Window → Devices and Simulators → tick
"Connect via network"), so the cable is only needed once.

## Connecting to the Mac

The phone must be on the same network as the inference Mac. Use the Mac's address (the part after `@` in the Mac
app's Settings → Connection, e.g. `192.168.1.49`) and the backend port (`8080`). If you set an API key in the Mac
app, enter it too. The backend must listen on the network (listen address `0.0.0.0`, not `127.0.0.1`).

## Layout

| Path | What |
|---|---|
| `project.yml` | xcodegen spec (`AIHarness.xcodeproj` is generated, not committed) |
| `Sources/` | SwiftUI app: `ChatEngine` (stream + tool loop), `API`, `WebTools`, `MarkdownView` (Markdown renderer), views |
| `tools/ios-icon.swift` | derives the iOS icon from the Mac icon drawing at install time |
