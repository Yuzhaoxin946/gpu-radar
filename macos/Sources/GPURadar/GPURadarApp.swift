import SwiftUI

@main
struct GPURadarApp: App {
    @StateObject private var store = RadarStore()

    var body: some Scene {
        WindowGroup("GPU Radar", id: "main") {
            MainView().environmentObject(store)
                .tint(Color(red: 0.15, green: 0.57, blue: 0.40))
        }
        .defaultSize(width: 1120, height: 780)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(after: .toolbar) {
                Button("刷新 GPU 状态") { Task { await store.refresh() } }
                    .keyboardShortcut("r")
                    .disabled(!store.authenticated)
            }
        }

        MenuBarExtra {
            MenuPanel().environmentObject(store)
                .tint(Color(red: 0.15, green: 0.57, blue: 0.40))
        } label: {
            Image(systemName: "square.grid.3x3.fill")
            Text("GPU")
        }
        .menuBarExtraStyle(.window)
    }
}
