import AppKit
import SwiftUI

enum MenuPanelLayout {
    static let width: CGFloat = 420
    static let rowHeight: CGFloat = 32
    static let maximumVisibleRows = 10
    static let padding: CGFloat = 12

    static func listHeight(serverCount: Int) -> CGFloat {
        CGFloat(min(max(serverCount, 0), maximumVisibleRows)) * rowHeight + padding * 2
    }
}

struct MenuPanel: View {
    @EnvironmentObject private var store: RadarStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if store.isDemo {
                Text("演示数据").font(.caption2).foregroundStyle(.orange)
                    .padding(.horizontal, MenuPanelLayout.padding).padding(.top, 12)
            } else if !store.authenticated {
                Text("请在主窗口登录账户。")
                    .font(.caption).foregroundStyle(.secondary).padding(14)
            } else if !store.connected {
                Text(store.isLoading ? "正在连接…" : "连接中断 · 当前状态未知")
                    .font(.caption2).foregroundStyle(.secondary)
                    .padding(.horizontal, MenuPanelLayout.padding).padding(.top, 12)
            }

            if store.authenticated || store.isDemo {
                if store.servers.isEmpty {
                    Text(store.isLoading ? "正在获取服务器…" : "暂无服务器，请在主窗口添加。")
                        .font(.caption).foregroundStyle(.secondary).padding(14)
                } else {
                    MenuServerList(servers: store.servers) { gpu, server in
                        store.state(of: gpu, in: server)
                    }
                }
            }

            Divider()
            HStack {
                Button("打开主窗口") { showMain() }.buttonStyle(.plain)
                Spacer()
                Button("退出") { NSApplication.shared.terminate(nil) }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
            }.font(.caption).padding(MenuPanelLayout.padding)
        }
        .frame(width: MenuPanelLayout.width)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func showMain() {
        openWindow(id: "main")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
}

struct MenuServerList: View {
    let servers: [GPUServer]
    let gpuState: (GPU, GPUServer) -> GPUState

    var body: some View {
        ScrollView(.vertical, showsIndicators: servers.count > MenuPanelLayout.maximumVisibleRows) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(servers.sorted(by: { $0.name.localizedStandardCompare($1.name) == .orderedAscending })) { server in
                    serverRow(server)
                        .frame(height: MenuPanelLayout.rowHeight)
                }
            }.padding(MenuPanelLayout.padding)
        }
        // MenuBarExtra sizes its window from the content's ideal size. A maximum
        // alone lets a ScrollView collapse to zero, leaving only footer buttons.
        .frame(width: MenuPanelLayout.width,
               height: MenuPanelLayout.listHeight(serverCount: servers.count))
    }

    private func serverRow(_ server: GPUServer) -> some View {
        HStack(spacing: 8) {
            Text(server.name).font(.system(size: 11, weight: .medium))
                .lineLimit(1).truncationMode(.middle)
                .frame(width: 84, alignment: .leading)
                .help(server.name)
            Text(modelLabel(server)).font(.system(size: 10))
                .foregroundStyle(.secondary).lineLimit(1)
                .frame(width: 112, alignment: .leading)
                .help(server.modelGroups.map(\.name).joined(separator: " / "))
            if server.gpus.isEmpty {
                Text("等待上报").font(.caption2).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 4) {
                        ForEach(server.gpus.sorted(by: { $0.index < $1.index })) { gpu in
                            RoundedRectangle(cornerRadius: 3)
                                .fill(gpuState(gpu, server).color)
                                .frame(width: 16, height: 16)
                                .accessibilityLabel("GPU \(gpu.index)，\(gpuState(gpu, server).title)")
                                .help("\(server.name) · GPU \(gpu.index) · \(gpuState(gpu, server).title)")
                        }
                    }.padding(.vertical, 4)
                }
                .frame(maxWidth: .infinity)
                .frame(height: 24)
            }
        }
    }

    private func modelLabel(_ server: GPUServer) -> String {
        server.modelGroups.map {
            $0.name.replacingOccurrences(of: "NVIDIA ", with: "")
                .replacingOccurrences(of: "GeForce ", with: "")
        }.joined(separator: " / ")
    }
}
