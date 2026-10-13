import AppKit
import SwiftUI

private let cardBackground = Color(nsColor: .controlBackgroundColor)
private let subtleLine = Color.primary.opacity(0.08)

struct MainView: View {
    @EnvironmentObject private var store: RadarStore
    var body: some View {
        Group {
            if store.authenticated || store.isDemo { DashboardView() }
            else { LoginView() }
        }
        .frame(minWidth: 1000, minHeight: 650)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct LoginView: View {
    @EnvironmentObject private var store: RadarStore
    @State private var password = ""
    @State private var passwordConfirmation = ""
    @State private var inviteCode = ""
    @State private var validationError: String?
    @State private var register = false
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 24) {
                Label("GPU RADAR", systemImage: "dot.radiowaves.left.and.right")
                    .font(.system(size: 14, weight: .bold, design: .rounded)).tracking(2)
                    .foregroundStyle(.green)
                Text("下一次实验，\n从空闲的 GPU 开始。")
                    .font(.system(size: 37, weight: .bold, design: .rounded)).lineSpacing(6)
                Text("把多台服务器收进一个雷达。\n扫一眼菜单栏，找到值得上机确认的卡。")
                    .font(.system(size: 15)).foregroundStyle(.secondary).lineSpacing(7)
                VStack(alignment: .leading, spacing: 15) {
                    previewRow("lab-a100", "A100 80GB", [.idle, .idle, .idle, .uncertain, .busy, .busy, .busy, .busy])
                    previewRow("vision-4090", "RTX 4090", [.idle, .idle, .busy, .busy])
                    previewRow("compute-04", "V100 32GB", [.unknown, .unknown, .unknown, .unknown])
                }
                .padding(20).background(cardBackground.opacity(0.8), in: RoundedRectangle(cornerRadius: 14))
                Text("上方仅为界面示意，不代表真实服务器状态。")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Label("每个账户独立管理自己的服务器", systemImage: "person.crop.circle.badge.checkmark")
                    .foregroundStyle(.secondary).font(.callout)
            }
            .padding(40).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(LinearGradient(colors: [Color.green.opacity(0.07), Color.clear], startPoint: .topLeading, endPoint: .bottomTrailing))
            ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(register ? "创建你的账户" : "欢迎回来").font(.system(size: 28, weight: .semibold))
                Text(register ? "注册需要邀请码，请向管理员获取。" : "登录后查看属于你的服务器。")
                    .font(.callout).foregroundStyle(.secondary)
                Picker("账户", selection: $register) {
                    Text("登录").tag(false)
                    Text("注册").tag(true)
                }.pickerStyle(.segmented).disabled(store.isAuthenticating)
                    .onChange(of: register) { _ in
                        validationError = nil
                        store.errorMessage = nil
                        passwordConfirmation = ""
                        inviteCode = ""
                    }
                VStack(alignment: .leading, spacing: 7) {
                    Text("账户名").font(.caption).foregroundStyle(.secondary)
                    TextField("researcher", text: $store.username).textFieldStyle(.roundedBorder)
                }
                VStack(alignment: .leading, spacing: 7) {
                    Text("密码").font(.caption).foregroundStyle(.secondary)
                    SecureField("输入账户密码", text: $password).textFieldStyle(.roundedBorder)
                        .onSubmit { submit() }
                    if register { Text("使用 10 至 128 个字符的密码。")
                        .font(.caption2).foregroundStyle(.secondary) }
                }
                if register {
                    VStack(alignment: .leading, spacing: 7) {
                        Text("确认密码").font(.caption).foregroundStyle(.secondary)
                        SecureField("再次输入密码", text: $passwordConfirmation).textFieldStyle(.roundedBorder)
                            .onSubmit { submit() }
                    }
                    VStack(alignment: .leading, spacing: 7) {
                        Text("邀请码").font(.caption).foregroundStyle(.secondary)
                        TextField("输入管理员提供的邀请码", text: $inviteCode).textFieldStyle(.roundedBorder)
                            .onSubmit { submit() }
                    }
                }
                if let validationError { ErrorBanner(text: validationError) }
                if let error = store.errorMessage { ErrorBanner(text: error) }
                Button(action: submit) {
                    HStack {
                        Spacer()
                        if store.isAuthenticating { ProgressView().controlSize(.small) }
                        Text(store.isAuthenticating ? "正在连接…" : register ? "创建账户并登录" : "登录雷达")
                        Spacer()
                    }.padding(.vertical, 5)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(store.isAuthenticating || store.username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty)
                Text("登录凭据保存在 macOS 钥匙串中；不保存密码。")
                    .font(.caption).foregroundStyle(.secondary)
                Divider().padding(.vertical, 2)
                Button { password = ""; passwordConfirmation = ""; inviteCode = ""; store.useDemo() } label: {
                    Label("先看看演示看板", systemImage: "play.rectangle")
                        .frame(maxWidth: .infinity)
                }.buttonStyle(.bordered).controlSize(.large).disabled(store.isAuthenticating)
            }.padding(.horizontal, 40).padding(.vertical, 28)
            }.frame(width: 400)
        }
    }
    private func submit() {
        guard !password.isEmpty else { return }
        validationError = nil
        if register {
            guard (10...128).contains(password.count) else { validationError = "密码需要 10 至 128 个字符。"; return }
            guard password == passwordConfirmation else { validationError = "两次输入的密码不一致。"; return }
            let normalizedInviteCode = inviteCode.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !normalizedInviteCode.isEmpty else { validationError = "请输入管理员提供的邀请码。"; return }
            guard normalizedInviteCode.count <= 128 else { validationError = "邀请码最多 128 个字符。"; return }
        }
        Task {
            if await store.login(password: password, register: register, inviteCode: inviteCode) {
                password = ""
                passwordConfirmation = ""
                inviteCode = ""
            }
        }
    }
    private func previewRow(_ name: String, _ model: String, _ states: [GPUState]) -> some View {
        HStack(spacing: 9) {
            Text(name).font(.system(size: 11, weight: .medium, design: .monospaced)).frame(width: 87, alignment: .leading)
            Text(model).font(.system(size: 10)).foregroundStyle(.secondary).frame(width: 66, alignment: .leading)
            HStack(spacing: 4) {
                ForEach(states.indices, id: \.self) { index in RoundedRectangle(cornerRadius: 3).fill(states[index].color).frame(width: 16, height: 16) }
            }
        }
    }
}

struct DashboardView: View {
    @EnvironmentObject private var store: RadarStore
    @State private var showEnrollment = false
    @State private var showPasswordChange = false
    @State private var serverToUnbind: GPUServer?
    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 9) {
                        Image(systemName: "dot.radiowaves.left.and.right").foregroundStyle(.green)
                        Text("GPU Radar").font(.system(size: 24, weight: .bold, design: .rounded))
                        if store.isDemo { DemoBadge() }
                    }
                    Label(store.statusText, systemImage: store.isDemo ? "sparkles" : store.connected ? "checkmark.circle.fill" : "wifi.slash")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if !store.isDemo { Text(store.username).font(.callout).foregroundStyle(.secondary) }
                if !store.isDemo {
                    Button("修改密码") { showPasswordChange = true }.disabled(store.isChangingPassword)
                }
                Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                    .help("刷新状态 ⌘R").disabled(store.isDemo || store.isLoading)
                Button { showEnrollment = true } label: { Label("添加服务器", systemImage: "plus") }
                    .buttonStyle(.borderedProminent).disabled(store.isDemo)
                Button(store.isDemo ? "退出演示" : "退出账户") { Task { await store.logout() } }
                    .disabled(store.isChangingPassword)
            }.padding(.horizontal, 26).padding(.vertical, 20)
            Divider()
            if store.isDemo {
                Label("当前展示示例数据。登录服务端并绑定 Linux 客户端后，才会显示真实 GPU 状态。", systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 26).padding(.vertical, 10).background(Color.orange.opacity(0.07))
            } else if let error = store.errorMessage {
                ErrorBanner(text: error).padding(.horizontal, 26).padding(.top, 12)
            }
            HStack(spacing: 14) {
                SummaryTile(value: "\(store.idleCards)", title: "空闲", caption: "近 5 分钟低负载且显存充足", color: GPUState.idle.color)
                SummaryTile(value: "\(store.totalCards)", title: "已登记 GPU", caption: "跨 \(store.servers.count) 台服务器", color: .primary)
                SummaryTile(value: "\(store.totalCards - store.knownCards)", title: "状态未知", caption: "采集中、离线或数据不完整", color: GPUState.unknown.color)
            }.padding(.horizontal, 26).padding(.top, 20).padding(.bottom, 18)
            filterBar.padding(.horizontal, 26).padding(.bottom, 14)
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("服务器").font(.headline)
                        Text("\(store.visibleServers.count)").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        StateLegend(compact: true)
                    }
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            if store.servers.isEmpty { emptyState }
                            else if store.visibleServers.isEmpty {
                                VStack(spacing: 12) {
                                    Image(systemName: "line.3.horizontal.decrease.circle").font(.largeTitle).foregroundStyle(.secondary)
                                    Text("没有符合条件的服务器").font(.headline)
                                    Text("试试降低显存或卡数要求，或清除筛选。")
                                        .font(.callout).foregroundStyle(.secondary)
                                    Button("清除筛选") { store.resetFilters() }
                                }.padding(35).frame(maxWidth: .infinity)
                            } else {
                                ForEach(store.visibleServers) { server in
                                    ServerCard(server: server, onUnbind: { serverToUnbind = server })
                                }
                            }
                        }.padding(.bottom, 14)
                    }
                }.frame(maxWidth: .infinity)
                GPUDetailPanel().frame(width: 300)
            }.padding(.horizontal, 26)
            HStack {
                Text("每个方格代表一张 GPU · 按卡号排列 · 点击查看详情")
                Spacer()
                Text("空闲不代表资源预留")
            }.font(.caption2).foregroundStyle(.secondary).padding(.horizontal, 26).padding(.vertical, 12)
        }
        .sheet(isPresented: $showEnrollment) { EnrollmentView().environmentObject(store) }
        .sheet(isPresented: $showPasswordChange) { PasswordChangeView().environmentObject(store) }
        .alert("解绑服务器？", isPresented: Binding(get: { serverToUnbind != nil }, set: { if !$0 { serverToUnbind = nil } })) {
            Button("取消", role: .cancel) { serverToUnbind = nil }
            Button("解绑", role: .destructive) {
                if let server = serverToUnbind { Task { await store.unbind(server) } }
                serverToUnbind = nil
            }
        } message: {
            Text("“\(serverToUnbind?.name ?? "")”将从本账户移除，客户端凭据立即失效。需要在 Linux 服务器上重新登录才能再次加入。")
        }
    }
    private var filterBar: some View {
        HStack(spacing: 12) {
            HStack(spacing: 7) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索服务器或型号", text: $store.search).textFieldStyle(.plain)
            }.padding(8).background(cardBackground, in: RoundedRectangle(cornerRadius: 7)).frame(minWidth: 130)
            Picker("型号", selection: $store.selectedModel) {
                Text("全部型号").tag("全部型号")
                ForEach(store.availableModels, id: \.self) { Text($0.replacingOccurrences(of: "NVIDIA ", with: "")).tag($0) }
            }.labelsHidden().frame(width: 185)
            Picker("剩余显存", selection: $store.minimumMemory) {
                Text("不限显存").tag(0.0)
                ForEach([8, 12, 16, 20, 24, 40, 48, 80], id: \.self) { Text("≥ \($0) GiB").tag(Double($0)) }
            }.labelsHidden().frame(width: 110)
            Picker("同机卡数", selection: $store.minimumCards) {
                Text("同机 ≥ 1 卡").tag(1)
                ForEach([2, 4, 8], id: \.self) { Text("同机 ≥ \($0) 卡").tag($0) }
            }.labelsHidden().frame(width: 110)
            Toggle("仅空闲", isOn: $store.candidatesOnly).toggleStyle(.checkbox).font(.caption)
        }.help("设置显存或多卡条件时，仅统计符合条件的空闲 GPU。")
    }
    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "server.rack").font(.system(size: 36)).foregroundStyle(.secondary)
            Text(store.isLoading ? "正在同步服务器…" : "让第一台服务器加入雷达").font(.headline)
            Text("在 Linux 服务器上安装客户端，\n使用同一账户登录后，这里会自动出现状态。")
                .multilineTextAlignment(.center).foregroundStyle(.secondary).font(.callout)
            Button("查看添加步骤") { showEnrollment = true }.buttonStyle(.borderedProminent)
        }.padding(40).frame(maxWidth: .infinity)
    }
}

struct ServerCard: View {
    @EnvironmentObject private var store: RadarStore
    let server: GPUServer
    let onUnbind: () -> Void
    private var idleCount: Int { server.gpus.filter { store.state(of: $0, in: server) == .idle }.count }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                HStack(spacing: 9) {
                    Image(systemName: "server.rack").font(.title3).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(server.name).font(.system(size: 14, weight: .semibold))
                        Text(server.hostname).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Text("\(idleCount) / \(server.gpus.count) 空闲").font(.caption).foregroundStyle(idleCount > 0 ? GPUState.idle.color : .secondary)
                Menu {
                    Button("复制 SSH 命令") { copyToClipboard("ssh -- " + shellQuote(server.hostname)) }
                    Divider()
                    Button("解绑服务器", role: .destructive, action: onUnbind).disabled(store.isDemo)
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize()
            }
            if server.gpus.isEmpty {
                Text("尚无 GPU 数据 · 等待客户端上报").font(.callout).foregroundStyle(.secondary).padding(.vertical, 5)
            } else {
                ForEach(server.modelGroups.filter { store.selectedModel == "全部型号" || $0.name == store.selectedModel }, id: \.name) { group in
                    HStack(alignment: .top, spacing: 13) {
                        Text(group.name.replacingOccurrences(of: "NVIDIA ", with: ""))
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
                            .frame(width: 137, alignment: .leading).padding(.top, 7)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 29, maximum: 29), spacing: 7)], alignment: .leading, spacing: 7) {
                            ForEach(group.gpus) { gpu in GPUCell(gpu: gpu, server: server, size: 28, showIndex: true) }
                        }
                    }
                }
            }
            HStack {
                Text(store.isDemo ? "示例快照" : "更新于 " + RadarStore.relativeTime(server.lastSeen, now: store.now))
                Spacer()
                if let error = server.error { Label(error, systemImage: "exclamationmark.triangle").lineLimit(1).help(error) }
                else if server.offline { Label("服务器离线", systemImage: "wifi.slash") }
            }.font(.caption2).foregroundStyle(.secondary)
        }.padding(17).background(cardBackground, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(subtleLine))
    }
}

struct GPUCell: View {
    @EnvironmentObject private var store: RadarStore
    let gpu: GPU
    let server: GPUServer
    let size: CGFloat
    var showIndex = false
    @State private var hovering = false
    private var state: GPUState { store.state(of: gpu, in: server) }
    private var selected: Bool { store.selection?.serverID == server.id && store.selection?.gpuID == gpu.uuid }
    var body: some View {
        Button { store.select(gpu, server: server) } label: {
            VStack(spacing: 4) {
                RoundedRectangle(cornerRadius: size > 20 ? 5 : 3)
                    .fill(state.color.opacity(hovering ? 0.76 : 1))
                    .frame(width: size, height: size)
                    .overlay(RoundedRectangle(cornerRadius: size > 20 ? 5 : 3).strokeBorder(Color.black.opacity(0.08)))
                    .overlay(RoundedRectangle(cornerRadius: size > 20 ? 7 : 5).stroke(selected ? Color.primary.opacity(0.8) : .clear, lineWidth: 1.5).padding(-3))
                    .overlay {
                        if state == .unknown { Text("?").font(.system(size: size * 0.58, weight: .medium)).foregroundStyle(.white.opacity(0.8)) }
                    }
                if showIndex { Text("\(gpu.index)").font(.system(size: 9, weight: .medium, design: .monospaced)).foregroundStyle(.secondary) }
            }.contentShape(Rectangle())
        }.buttonStyle(.plain)
            .onHover { hovering = $0 }
            .help("\(server.name) · GPU \(gpu.index) · \(state.title)\n\(gpu.shortName)\n利用率 \(gpu.utilizationLabel) · 显存 \(gpu.memoryLabel)")
            .accessibilityLabel("\(server.name)，GPU \(gpu.index)，\(gpu.shortName)，\(state.title)，利用率 \(gpu.utilizationLabel)，显存 \(gpu.memoryLabel)")
    }
}

struct GPUDetailPanel: View {
    @EnvironmentObject private var store: RadarStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("GPU 详情").font(.headline)
                if let selected = store.selectedVisibleGPU {
                    let gpu = selected.gpu
                    let server = selected.server
                    let state = store.state(of: gpu, in: server)
                    VStack(alignment: .leading, spacing: 8) {
                        Text(server.name + " / GPU \(gpu.index)").font(.system(size: 19, weight: .semibold, design: .rounded))
                        Text(gpu.shortName).font(.callout).foregroundStyle(.secondary)
                        HStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 3).fill(state.color).frame(width: 10, height: 10)
                            Text(state.title).font(.caption).foregroundStyle(state.color)
                        }
                    }
                    if state == .unknown { Label("以下是最近采样值，当前状态未知。", systemImage: "clock.badge.exclamationmark")
                        .font(.caption).foregroundStyle(.secondary) }
                    Divider()
                    metric("近 5 分钟平均利用率", gpu.averageUtilizationLabel, value: gpu.utilizationAvg5MPercent.map { $0 / 100 }, color: state.color)
                    if !gpu.hasCompleteWindow {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(server.offline || (!store.isDemo && !store.connected) ? "采集暂停 · 5 分钟窗口尚不完整" : "正在收集完整的 5 分钟数据")
                                .font(.caption).foregroundStyle(.secondary)
                            ProgressView(value: min(300, max(0, gpu.utilizationWindowSeconds ?? 0)), total: 300).tint(.gray)
                            Text(gpu.collectionProgress).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    metric("当前计算利用率", gpu.utilizationLabel, value: gpu.utilizationGpuPercent.map { $0 / 100 }, color: state.color)
                    metric("已用 / 总显存", gpu.memoryLabel, value: memoryFraction(gpu), color: .blue)
                    if let free = gpu.freeGiB {
                        HStack { Text("剩余显存"); Spacer(); Text(String(format: "%.1f GiB", free)).fontWeight(.medium) }.font(.callout)
                    }
                    HStack(spacing: 24) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("温度").font(.caption).foregroundStyle(.secondary)
                            Text(gpu.temperatureCelsius.map { String(format: "%.0f °C", $0) } ?? "—")
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            Text("功耗").font(.caption).foregroundStyle(.secondary)
                            Text(gpu.powerWatts.map { String(format: "%.0f W", $0) } ?? "—")
                        }
                    }
                    Divider()
                    VStack(alignment: .leading, spacing: 10) {
                        Text("计算进程").font(.subheadline).fontWeight(.semibold)
                        if !gpu.processesKnown { Text("进程信息不可用，不能据此判断空闲。").font(.caption).foregroundStyle(.secondary) }
                        else if gpu.processes.isEmpty { Label("采样时无计算进程", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary) }
                        else {
                            ForEach(gpu.processes) { process in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack {
                                        Label(process.username ?? "用户未知", systemImage: "person")
                                        Spacer()
                                        Text(process.memoryUsedMib.map { String(format: "%.1f GiB", $0 / 1024) } ?? "—")
                                    }.font(.caption)
                                    Text("\(process.name ?? "进程未知") · PID \(process.pid)")
                                        .font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                                }.padding(9).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 7))
                            }
                        }
                    }
                    Divider()
                    VStack(alignment: .leading, spacing: 7) {
                        if state == .idle, let since = gpu.idleSince {
                            Text("连续空闲：" + idleDuration(since)).foregroundStyle(GPUState.idle.color)
                            Text("空闲起始：" + since.formatted(date: .omitted, time: .standard))
                        }
                        Text(store.isDemo ? "数据来源：演示快照" : "采样时间：" + (server.sampledAt?.formatted(date: .abbreviated, time: .standard) ?? "尚未上报"))
                        Text("UUID：\(gpu.uuid)").textSelection(.enabled).font(.system(size: 10, design: .monospaced))
                    }.font(.caption).foregroundStyle(.secondary)
                    HStack {
                        CopyButton("SSH 命令", value: "ssh -- " + shellQuote(server.hostname))
                        CopyButton("GPU UUID", value: gpu.uuid)
                    }
                    Text("在服务器上重新确认后再启动训练；看板不会预留或占用 GPU。")
                        .font(.caption2).foregroundStyle(.secondary)
                    Text("近 5 分钟均值 ≥ 10%：使用中；均值 < 10% 且剩余显存 > 90%：空闲；其余为被占用。")
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 12) {
                        Image(systemName: "cursorarrow.click.2").font(.system(size: 30)).foregroundStyle(.secondary)
                        Text("选择一个 GPU 方格").font(.callout)
                        Text("查看显存、计算利用率和 Linux 用户。")
                            .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }.frame(maxWidth: .infinity).padding(.vertical, 70)
                }
            }.padding(18)
        }.background(cardBackground, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(subtleLine))
    }
    private func metric(_ title: String, _ valueText: String, value: Double?, color: Color) -> some View {
        VStack(spacing: 8) {
            HStack { Text(title).foregroundStyle(.secondary); Spacer(); Text(valueText).fontWeight(.medium) }.font(.caption)
            if let value { ProgressView(value: min(1, max(0, value))).tint(color) }
        }
    }
    private func memoryFraction(_ gpu: GPU) -> Double? {
        guard let used = gpu.memoryUsedMib, let total = gpu.memoryTotalMib, total > 0 else { return nil }
        return used / total
    }
    private func idleDuration(_ since: Date) -> String {
        let seconds = max(0, Int(store.now.timeIntervalSince(since)))
        if seconds < 60 { return "\(seconds) 秒" }
        if seconds < 3600 { return "\(seconds / 60) 分 \(seconds % 60) 秒" }
        return "\(seconds / 3600) 小时 \((seconds % 3600) / 60) 分"
    }
}

struct SummaryTile: View {
    let value: String
    let title: String
    let caption: String
    let color: Color
    var body: some View {
        HStack(spacing: 14) {
            Text(value).font(.system(size: 33, weight: .semibold, design: .rounded)).monospacedDigit().foregroundStyle(color)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.callout).fontWeight(.medium)
                Text(caption).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.padding(17).frame(maxWidth: .infinity)
            .background(cardBackground, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(subtleLine))
    }
}

struct StateLegend: View {
    var compact = false
    var body: some View {
        HStack(spacing: compact ? 9 : 14) {
            ForEach(GPUState.allCases, id: \.self) { state in
                HStack(spacing: 4) {
                    RoundedRectangle(cornerRadius: 2).fill(state.color).frame(width: 8, height: 8)
                    Text(state.title)
                }
            }
        }.font(.system(size: compact ? 9 : 10)).foregroundStyle(.secondary)
    }
}

struct DemoBadge: View {
    var body: some View {
        Text("演示").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
            .padding(.horizontal, 7).padding(.vertical, 3).background(Color.orange.opacity(0.12), in: Capsule())
    }
}

struct ErrorBanner: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle")
            .font(.caption).foregroundStyle(.orange)
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            .textSelection(.enabled)
    }
}

struct CopyButton: View {
    let title: String
    let value: String
    @State private var copied = false
    init(_ title: String, value: String) { self.title = title; self.value = value }
    var body: some View {
        Button {
            copyToClipboard(value)
            copied = true
            Task { try? await Task.sleep(nanoseconds: 1_800_000_000); copied = false }
        } label: { Label(copied ? "已复制" : title, systemImage: copied ? "checkmark" : "doc.on.doc") }
        .font(.caption)
    }
}

func copyToClipboard(_ value: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(value, forType: .string)
}

func shellQuote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
