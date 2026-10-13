import SwiftUI

enum AgentDownloadSource: String, CaseIterable, Identifiable {
    case github = "GitHub"
    case aliyun = "阿里云镜像"

    var id: Self { self }
}

struct EnrollmentCommands {
    let username: String
    let serverName: String

    private var name: String { serverName.trimmingCharacters(in: .whitespacesAndNewlines) }

    var nameIsValid: Bool {
        name.unicodeScalars.count <= 80 && !name.unicodeScalars.contains(where: { $0.value < 32 })
    }

    func install(from source: AgentDownloadSource) -> String {
        let url = source == .github
            ? "https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh"
            : API.baseURL + "/agent/install.sh"
        let mirror = source == .aliyun ? " --mirror aliyun" : ""
        let displayName = name.isEmpty ? "" : " --name " + shellQuote(name)
        return "curl -fsSL \(url) | bash -s --\(mirror) --username \(shellQuote(username))\(displayName)"
    }

    var login: String {
        let displayName = name.isEmpty ? "\"$(hostname -s)\"" : shellQuote(name)
        // This uses the installer's per-host paths. The CLI refuses an existing
        // config before asking for a password, preserving the original binding.
        return """
        (
          machine_id="$(cat /etc/machine-id)" &&
          python3 -c 'import re, sys; sys.exit(0 if re.fullmatch(r"[0-9a-f]{32}", sys.argv[1]) and sys.argv[1] != "0" * 32 else "Invalid machine ID")' "$machine_id" &&
          { test -f "$HOME/.local/share/gpu-radar/hosts/$machine_id/gpu-radar-agent.pyz" || { printf '%s\\n' 'Agent not installed. Run the one-click installer above.' >&2; exit 1; }; } &&
          python3 "$HOME/.local/share/gpu-radar/hosts/$machine_id/gpu-radar-agent.pyz" login \\
            --config "$HOME/.config/gpu-radar/hosts/$machine_id/agent.json" \\
            --server \(shellQuote(API.baseURL)) \\
            --username \(shellQuote(username)) --name \(displayName)
        )
        """
    }
}

struct EnrollmentView: View {
    @EnvironmentObject private var store: RadarStore
    @Environment(\.dismiss) private var dismiss
    @State private var serverName = ""
    @State private var source: AgentDownloadSource = .aliyun
    @State private var showLogin = false

    private var commands: EnrollmentCommands {
        EnrollmentCommands(username: store.username, serverName: serverName)
    }

    private var canCopy: Bool { commands.nameIsValid && !store.username.isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("添加 Linux 服务器").font(.title2).fontWeight(.semibold)
                    Text("在目标服务器的 SSH 终端粘贴命令，即可加入当前账户。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                Button { dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).accessibilityLabel("关闭添加服务器")
            }.padding(24)
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 12) {
                        LabeledContent("绑定账户") {
                            Text(store.username).fontWeight(.medium).textSelection(.enabled)
                        }
                        HStack {
                            Text("服务器名称").foregroundStyle(.secondary)
                            TextField("可选，默认使用主机名", text: $serverName)
                                .textFieldStyle(.roundedBorder)
                        }
                        if !commands.nameIsValid {
                            Text("名称最多 80 个字符，不能包含换行或控制字符。")
                                .font(.caption).foregroundStyle(.red)
                        }
                    }.font(.callout)

                    VStack(alignment: .leading, spacing: 12) {
                        Text("一键安装并登录").font(.headline)
                        Text("需要 Python 3.6+、正常工作的 NVIDIA 驱动和用户级 systemd。普通用户或 root 均可，安装到实际运行账户的主目录。")
                            .font(.callout).foregroundStyle(.secondary)
                        Picker("下载来源", selection: $source) {
                            ForEach(AgentDownloadSource.allCases) { item in
                                Text(item.rawValue).tag(item)
                            }
                        }.pickerStyle(.segmented)
                        commandBlock(commands.install(from: source), copyTitle: "复制一键安装命令")
                        Text("按提示输入当前 App 账户的密码。安装器会完成登录、首次上报与后台启动；退出 SSH 或重启服务器后也会继续采集。")
                            .font(.callout).foregroundStyle(.secondary)
                        Text("GitHub 下载不通时，切换为阿里云镜像后重新复制。安装成功后无需再执行下面的登录命令。")
                            .font(.caption).foregroundStyle(.secondary)
                    }

                    DisclosureGroup("单独登录命令（已安装、尚未绑定时）", isExpanded: $showLogin) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("仅适用于客户端文件已就位、但尚未绑定账户的服务器（例如手动部署）。安装中断或文件缺失时，请直接重试上方的一键安装。已有绑定时此命令会停止，不会覆盖或重复添加。")
                                .font(.callout).foregroundStyle(.secondary)
                            commandBlock(commands.login, copyTitle: "复制登录命令")
                            Text("单独登录不会启动后台采集。成功后再执行上方的一键安装命令，完成后台配置；重试会保留已有绑定。修改 App 密码后，无需重新绑定正在上报的服务器。")
                                .font(.caption).foregroundStyle(.secondary)
                        }.padding(.top, 10)
                    }.font(.callout)

                    Text("安装成功后服务器会自动出现。首次接入需要积累完整 5 分钟的数据，随后显示状态颜色。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(24)
            }.frame(height: showLogin ? 490 : 420)

            Divider()
            HStack {
                Spacer()
                Button("完成") { dismiss() }.buttonStyle(.borderedProminent)
            }.padding(.horizontal, 24).padding(.vertical, 16)
        }.frame(width: 570)
    }

    private func commandBlock(_ command: String, copyTitle: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(command).font(.system(size: 11, design: .monospaced))
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            CopyButton(copyTitle, value: command).disabled(!canCopy)
        }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 9))
    }
}
