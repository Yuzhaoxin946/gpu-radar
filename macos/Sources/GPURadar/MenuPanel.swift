import AppKit
import SwiftUI

struct MenuPanel: View {
    @EnvironmentObject private var store: RadarStore
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if store.isDemo {
                Text("演示数据").font(.caption2).foregroundStyle(.orange)
                    .padding(.horizontal, 14).padding(.top, 12)
            } else if !store.authenticated {
                Text("请在主窗口登录账户。")
                    .font(.caption).foregroundStyle(.secondary).padding(14)
            } else if !store.connected {
                Text(store.isLoading ? "正在连接…" : "连接中断 · 当前状态未知")
                    .font(.caption2).foregroundStyle(.secondary)
                    .padding(.horizontal, 14).padding(.top, 12)
            }
            if store.authenticated || store.isDemo {
                if store.servers.isEmpty {
                    Text(store.isLoading ? "正在获取服务器…" : "暂无服务器，请在主窗口添加。")
                        .font(.caption).foregroundStyle(.secondary).padding(14)
                } else {
                    ScrollView(.vertical) {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(store.servers.sorted(by: { $0.name.localizedStandardCompare($1.name) == .orderedAscending })) { server in
                                HStack(spacing: 12) {
                                    Text(server.name).font(.system(size: 11, weight: .medium))
                                        .lineLimit(1).truncationMode(.middle)
                                        .frame(width: 112, alignment: .leading)
                                    Text(modelLabel(server)).font(.system(size: 10))
                                        .foregroundStyle(.secondary).lineLimit(1)
                                        .frame(width: 145, alignment: .leading)
                                    if server.gpus.isEmpty {
                                        Text("等待上报").font(.caption2).foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                    } else {
                                        ScrollView(.horizontal, showsIndicators: false) {
                                            HStack(spacing: 6) {
                                                ForEach(server.gpus.sorted(by: { $0.index < $1.index })) { gpu in
                                                    RoundedRectangle(cornerRadius: 3)
                                                        .fill(store.state(of: gpu, in: server).color)
                                                        .frame(width: 18, height: 18)
                                                        .accessibilityLabel("GPU \(gpu.index)，\(store.state(of: gpu, in: server).title)")
                                                }
                                            }.padding(.vertical, 2)
                                        }.frame(height: 22)
                                    }
                                }
                            }
                        }.padding(14)
                    }.frame(maxHeight: 350)
                }
            }
            Divider()
            HStack {
                Button("打开主窗口") { showMain() }.buttonStyle(.plain)
                Spacer()
                Button("退出") { NSApplication.shared.terminate(nil) }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
            }.font(.caption).padding(14)
        }.frame(width: 555)
    }
    private func modelLabel(_ server: GPUServer) -> String {
        server.modelGroups.map { $0.name.replacingOccurrences(of: "NVIDIA ", with: "") }.joined(separator: " / ")
    }
    private func showMain() {
        openWindow(id: "main")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
}

struct EnrollmentView: View {
    @EnvironmentObject private var store: RadarStore
    @Environment(\.dismiss) private var dismiss
    @State private var serverName = "lab-gpu-01"
    private var command: String {
        "gpu-radar-agent login --server " + shellQuote(store.baseURL) + " --name " + shellQuote(serverName) + " --username " + shellQuote(store.username)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("添加 Linux 服务器").font(.title2).fontWeight(.semibold)
                    Text("使用同一个账户登录客户端，服务器便会自动加入此看板。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
            }
            Divider()
            step("1", "安装客户端", "通过 SSH 登录 GPU 服务器，按照项目 README 的 Linux Agent 步骤安装。需要 Python 3.10+ 与正常工作的 NVIDIA 驱动。")
            step("2", "用本账户绑定", "复制下面的命令。客户端会交互式询问密码，使用与 Mac 相同的账户登录。")
            HStack {
                Text("服务器名称").font(.caption).foregroundStyle(.secondary)
                TextField("lab-gpu-01", text: $serverName).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 12) {
                Text(command).font(.system(size: 12, design: .monospaced)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                CopyButton("复制登录命令", value: command)
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 9))
            step("3", "启动后台采集", "先运行 gpu-radar-agent run 验证采集，再按 README 配置 systemd 服务。客户端保存可撤销的设备凭据，重新启动无需再次输入密码。")
            Text("服务器应能主动访问服务端。服务端不会持有你的 SSH 密钥，也不会代你执行训练命令。")
                .font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("完成") { dismiss() }.buttonStyle(.borderedProminent) }
        }.padding(28).frame(width: 610)
    }
    private func step(_ number: String, _ title: String, _ description: String) -> some View {
        HStack(alignment: .top, spacing: 13) {
            Text(number).font(.system(size: 12, weight: .semibold, design: .rounded)).frame(width: 25, height: 25)
                .background(Color.green.opacity(0.10), in: Circle()).foregroundStyle(.green)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(description).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
