import AppKit
import Combine
import Foundation

@MainActor
final class RadarStore: ObservableObject {
    let baseURL = API.baseURL
    @Published var username = ""
    @Published var servers: [GPUServer] = []
    @Published var isDemo = false
    @Published var authenticated = false
    @Published var isLoading = false
    @Published var isAuthenticating = false
    @Published var isChangingPassword = false
    @Published var errorMessage: String?
    @Published var connected = false
    @Published var lastSuccess: Date?
    @Published var now = Date()
    @Published var selection: (serverID: String, gpuID: String)?
    @Published var search = ""
    @Published var selectedModel = "全部型号"
    @Published var minimumMemory = 0.0
    @Published var minimumCards = 1
    @Published var candidatesOnly = false
    private var token: String?
    private var pollTask: Task<Void, Never>?
    private var clockTask: Task<Void, Never>?
    private var wakeObserver: NSObjectProtocol?
    private var sleepObserver: NSObjectProtocol?
    private var generation = UUID()
    private var snapshotRevision = 0
    private var lastSuccessInstant: ContinuousClock.Instant?
    private var serverTimeAnchor: Date?
    private var serverTimeInstant: ContinuousClock.Instant?

    init() {
        UserDefaults.standard.removeObject(forKey: "backendURL")
        clockTask = Task { [weak self] in
            while !Task.isCancelled {
                if let self { self.now = self.estimatedServerTime }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.authenticated, !self.isDemo else { return }
                self.invalidateSnapshot()
                self.now = self.estimatedServerTime
                await self.refresh()
            }
        }
        sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, !self.isDemo else { return }
                self.invalidateSnapshot()
            }
        }
        Task { [weak self] in await self?.restoreSavedSession() }
    }

    private func restoreSavedSession() async {
        let initialGeneration = generation
        do {
            let saved = try await SessionKeychain.load()
            guard generation == initialGeneration, !isAuthenticating, !isDemo else { return }
            if let saved {
                guard API.isBuiltInServer(saved.baseURL) else {
                    try? await SessionKeychain.delete()
                    if generation == initialGeneration { errorMessage = "已清除旧服务端的登录信息，请登录当前 GPU Radar 账户。" }
                    return
                }
                token = saved.token
                username = saved.username
                authenticated = true
                startPolling()
            }
        } catch {
            guard generation == initialGeneration, !isAuthenticating, !isDemo else { return }
            errorMessage = "无法自动恢复钥匙串中的会话，请重新登录。重新编译后的 App 可能需要 macOS 再次授权访问钥匙串。"
        }
    }

    var availableModels: [String] { Array(Set(servers.flatMap { $0.gpus.map(\.name) })).sorted() }
    var totalCards: Int { servers.reduce(0) { $0 + $1.gpus.count } }
    var idleCards: Int { servers.reduce(0) { count, server in count + server.gpus.filter { state(of: $0, in: server) == .idle }.count } }
    var knownCards: Int { servers.reduce(0) { count, server in count + server.gpus.filter { state(of: $0, in: server) != .unknown }.count } }
    var statusText: String {
        if isDemo { return "演示模式 · 示例数据" }
        if !authenticated { return "尚未登录" }
        if !connected { return isLoading ? "正在连接服务端…" : "连接中断 · 缓存状态未知" }
        if !cacheIsFresh { return "数据已过期 · 等待更新" }
        return "已连接 · 每 5 秒更新"
    }

    func state(of gpu: GPU, in server: GPUServer) -> GPUState {
        guard gpu.hasCompleteWindow else { return .unknown }
        if isDemo { return gpu.state }
        guard connected, cacheIsFresh,
              !server.offline, server.collectionStatus == "ok",
              let seen = server.lastSeen, let sampled = server.sampledAt,
              now.timeIntervalSince(seen) <= 30, now.timeIntervalSince(sampled) <= 30,
              seen.timeIntervalSince(now) <= 30, sampled.timeIntervalSince(now) <= 30 else { return .unknown }
        return gpu.state
    }

    private var cacheIsFresh: Bool {
        guard let lastSuccessInstant else { return false }
        return lastSuccessInstant.duration(to: .now) <= .seconds(30)
    }

    private var estimatedServerTime: Date {
        guard let serverTimeAnchor, let serverTimeInstant else { return Date() }
        let elapsed = serverTimeInstant.duration(to: .now).components
        return serverTimeAnchor.addingTimeInterval(Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18)
    }

    private func invalidateSnapshot() {
        snapshotRevision += 1
        isLoading = false
        connected = false
        lastSuccess = nil
        lastSuccessInstant = nil
    }

    func matches(_ gpu: GPU, in server: GPUServer) -> Bool {
        guard selectedModel == "全部型号" || selectedModel == gpu.name else { return false }
        guard state(of: gpu, in: server) == .idle else { return false }
        if minimumMemory > 0 { return (gpu.freeGiB ?? -1) >= minimumMemory }
        return true
    }

    var visibleServers: [GPUServer] {
        servers.filter { server in
            let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
            guard query.isEmpty || server.name.localizedCaseInsensitiveContains(query) || server.hostname.localizedCaseInsensitiveContains(query) || server.gpus.contains(where: { $0.name.localizedCaseInsensitiveContains(query) }) else { return false }
            guard selectedModel == "全部型号" || server.gpus.contains(where: { $0.name == selectedModel }) else { return false }
            if candidatesOnly || minimumMemory > 0 || minimumCards > 1 {
                return server.gpus.filter { matches($0, in: server) }.count >= minimumCards
            }
            return true
        }.sorted { a, b in
            let aCount = a.gpus.filter { matches($0, in: a) }.count
            let bCount = b.gpus.filter { matches($0, in: b) }.count
            return aCount == bCount ? a.name.localizedStandardCompare(b.name) == .orderedAscending : aCount > bCount
        }
    }

    var selectedGPU: (server: GPUServer, gpu: GPU)? {
        guard let selection, let server = servers.first(where: { $0.id == selection.serverID }), let gpu = server.gpus.first(where: { $0.uuid == selection.gpuID }) else { return nil }
        return (server, gpu)
    }

    // The menu shows every server; the detailed dashboard respects its filters.
    var selectedVisibleGPU: (server: GPUServer, gpu: GPU)? {
        guard let selected = selectedGPU,
              visibleServers.contains(where: { $0.id == selected.server.id }),
              selectedModel == "全部型号" || selected.gpu.name == selectedModel else { return nil }
        return selected
    }

    func select(_ gpu: GPU, server: GPUServer) { selection = (server.id, gpu.uuid) }

    func login(password: String, register: Bool) async -> Bool {
        guard !isAuthenticating else { return false }
        generation = UUID()
        let loginGeneration = generation
        isAuthenticating = true
        errorMessage = nil
        defer { isAuthenticating = false }
        do {
            let url = API.baseURL
            let user = username.trimmingCharacters(in: .whitespacesAndNewlines)
            let body = try JSONEncoder().encode(Credentials(username: user, password: password))
            let data = try await API.request(path: register ? "/auth/register" : "/auth/login", method: "POST", body: body)
            guard generation == loginGeneration else { return false }
            let response = try API.decoder.decode(LoginResponse.self, from: data)
            do { try await SessionKeychain.save(.init(baseURL: url, token: response.accessToken, username: response.account.username)) }
            catch {
                _ = try? await API.request(path: "/auth/logout", method: "POST", token: response.accessToken)
                throw error
            }
            guard generation == loginGeneration else {
                _ = try? await API.request(path: "/auth/logout", method: "POST", token: response.accessToken)
                return false
            }
            generation = UUID()
            username = response.account.username
            token = response.accessToken
            authenticated = true
            isDemo = false
            servers = []
            selection = nil
            connected = false
            lastSuccessInstant = nil
            serverTimeAnchor = nil
            serverTimeInstant = nil
            startPolling()
            return true
        } catch { if generation == loginGeneration { errorMessage = error.localizedDescription }; return false }
    }

    func changePassword(currentPassword: String, newPassword: String) async -> Bool {
        guard authenticated, !isDemo, !isChangingPassword, let previousToken = token else { return false }
        isChangingPassword = true
        errorMessage = nil
        generation = UUID()
        let passwordGeneration = generation
        pollTask?.cancel()
        pollTask = nil
        invalidateSnapshot()
        var replacementToken: String?
        defer {
            if generation == passwordGeneration {
                isChangingPassword = false
                if authenticated, token != nil { startPolling() }
            }
        }
        do {
            let body = try JSONEncoder().encode(PasswordChangeCredentials(currentPassword: currentPassword, newPassword: newPassword))
            let data = try await API.request(path: "/auth/password", method: "POST", token: previousToken, body: body)
            let response = try API.decoder.decode(LoginResponse.self, from: data)
            guard generation == passwordGeneration else {
                _ = try? await API.request(path: "/auth/logout", method: "POST", token: response.accessToken)
                return false
            }
            replacementToken = response.accessToken
            try await SessionKeychain.save(.init(baseURL: API.baseURL, token: response.accessToken, username: response.account.username))
            guard generation == passwordGeneration else {
                _ = try? await API.request(path: "/auth/logout", method: "POST", token: response.accessToken)
                return false
            }
            token = response.accessToken
            username = response.account.username
            errorMessage = nil
            return true
        } catch {
            guard generation == passwordGeneration else { return false }
            // An explicit 4xx rejection leaves the old password unchanged. Any
            // uncertain response or failed secure persistence must end this session.
            if replacementToken == nil, case let APIError.server(code, _) = error, (400..<500).contains(code), code != 401 {
                errorMessage = error.localizedDescription
            } else {
                let clearedGeneration = await clearSession()
                if let replacementToken {
                    _ = try? await API.request(path: "/auth/logout", method: "POST", token: replacementToken)
                }
                if generation == clearedGeneration {
                    errorMessage = replacementToken == nil
                        ? "密码修改结果无法确认，请重新登录。若旧密码失效，请使用新密码。"
                        : "密码已更新，但无法安全保存新会话，请使用新密码重新登录。"
                }
            }
            return false
        }
    }

    private func startPolling() {
        pollTask?.cancel()
        let sessionGeneration = generation
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.generation == sessionGeneration, self.authenticated, !self.isDemo else { break }
                await self.refresh()
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    func refresh() async {
        guard authenticated, !isDemo, !isLoading, !isChangingPassword, let token else { return }
        let currentGeneration = generation
        let currentRevision = snapshotRevision
        isLoading = true
        defer { if generation == currentGeneration && snapshotRevision == currentRevision { isLoading = false } }
        do {
            let data = try await API.request(path: "/servers", token: token)
            guard generation == currentGeneration, snapshotRevision == currentRevision, !Task.isCancelled else { return }
            let response = try API.decoder.decode(ServersResponse.self, from: data)
            servers = response.servers
            let receivedAt = ContinuousClock.now
            if let serverTime = response.serverTime {
                serverTimeAnchor = serverTime
                serverTimeInstant = receivedAt
            }
            lastSuccessInstant = receivedAt
            now = estimatedServerTime
            lastSuccess = now
            connected = true
            errorMessage = nil
            if let selection, !servers.contains(where: { $0.id == selection.serverID && $0.gpus.contains(where: { $0.uuid == selection.gpuID }) }) { self.selection = nil }
        } catch {
            guard generation == currentGeneration, snapshotRevision == currentRevision, !Task.isCancelled else { return }
            connected = false
            errorMessage = error.localizedDescription
            if (error as? APIError)?.unauthorized == true {
                let clearedGeneration = await clearSession()
                if generation == clearedGeneration { errorMessage = "登录已过期，请重新登录。" }
            }
        }
    }

    func logout() async {
        if isDemo { await clearSession(); return }
        let oldToken = token
        let logoutGeneration = await clearSession()
        guard let oldToken else { return }
        do { _ = try await API.request(path: "/auth/logout", method: "POST", token: oldToken) }
        catch { if generation == logoutGeneration { errorMessage = "已退出本机登录；服务端会话撤销失败，将在到期后失效。" } }
    }

    @discardableResult
    private func clearSession() async -> UUID {
        generation = UUID()
        pollTask?.cancel()
        pollTask = nil
        token = nil
        authenticated = false
        isDemo = false
        connected = false
        isLoading = false
        isChangingPassword = false
        lastSuccess = nil
        lastSuccessInstant = nil
        serverTimeAnchor = nil
        serverTimeInstant = nil
        servers = []
        selection = nil
        errorMessage = nil
        resetFilters()
        let clearingGeneration = generation
        do { try await SessionKeychain.delete() }
        catch { if generation == clearingGeneration { errorMessage = error.localizedDescription } }
        return clearingGeneration
    }

    func unbind(_ server: GPUServer) async {
        guard authenticated, !isDemo, !isChangingPassword, let token else { return }
        let currentGeneration = generation
        do {
            var allowed = CharacterSet.urlPathAllowed
            allowed.remove(charactersIn: "/?#%")
            guard let escaped = server.id.addingPercentEncoding(withAllowedCharacters: allowed) else { return }
            _ = try await API.request(path: "/servers/" + escaped, method: "DELETE", token: token)
            guard generation == currentGeneration else { return }
            // A GET started before the deletion must not restore the removed row.
            snapshotRevision += 1
            isLoading = false
            servers.removeAll { $0.id == server.id }
            if selection?.serverID == server.id { selection = nil }
        } catch { if generation == currentGeneration { errorMessage = error.localizedDescription } }
    }

    func resetFilters() {
        search = ""
        selectedModel = "全部型号"
        minimumMemory = 0
        minimumCards = 1
        candidatesOnly = false
    }

    func useDemo() {
        pollTask?.cancel()
        generation = UUID()
        isDemo = true
        authenticated = false
        connected = false
        token = nil
        errorMessage = nil
        isLoading = false
        lastSuccessInstant = nil
        serverTimeAnchor = nil
        serverTimeInstant = nil
        now = Date()
        resetFilters()
        servers = DemoData.servers()
        if let server = servers.first, let gpu = server.gpus.first { select(gpu, server: server) }
    }

    static func relativeTime(_ date: Date?, now: Date) -> String {
        guard let date else { return "尚未上报" }
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        if seconds < 60 { return "\(seconds) 秒前" }
        if seconds < 3600 { return "\(seconds / 60) 分钟前" }
        if seconds < 86400 { return "\(seconds / 3600) 小时前" }
        return "\(seconds / 86400) 天前"
    }
}

enum DemoData {
    static func servers() -> [GPUServer] {
        let now = Date()
        func gpu(_ index: Int, _ model: String, _ memory: Double, _ state: GPUState) -> GPU {
            let usage: Double = state == .busy ? 0.91 : state == .uncertain ? 0.35 : state == .unknown ? 0.50 : 0.004
            let processes: [GPUProcess] = state == .busy || state == .uncertain ? [.init(pid: 24510 + index, username: index % 2 == 0 ? "alice" : "bob", name: "python", memoryUsedMib: memory * usage - 200)] : []
            return GPU(uuid: "DEMO-\(model)-\(index)", index: index, name: model, memoryTotalMib: memory, memoryUsedMib: memory * usage, utilizationGpuPercent: state == .busy ? 96 : state == .uncertain ? 2 : 0, utilizationAvg5MPercent: state == .unknown ? nil : state == .busy ? 72 : state == .uncertain ? 3 : 0.5, utilizationWindowSeconds: state == .unknown ? 150 : 300, temperatureCelsius: state == .busy ? 72 : 38, powerWatts: state == .busy ? 310 : 55, processesKnown: state != .unknown, processes: processes, state: state, idleSince: state == .idle ? now.addingTimeInterval(-385) : nil)
        }
        func server(_ id: String, _ name: String, _ gpus: [GPU], offline: Bool = false) -> GPUServer {
            GPUServer(id: id, name: name, hostname: name + ".example", createdAt: now, lastSeen: offline ? now.addingTimeInterval(-180) : now, sampledAt: now, collectionStatus: "ok", error: nil, offline: offline, gpus: gpus)
        }
        return [
            server("demo-a", "lab-a100", (0..<8).map { gpu($0, "NVIDIA A100-SXM4-80GB", 81920, $0 < 3 ? .idle : $0 < 5 ? .uncertain : .busy) }),
            server("demo-b", "vision-4090", (0..<4).map { gpu($0, "NVIDIA GeForce RTX 4090", 24576, $0 < 2 ? .idle : .busy) }),
            server("demo-c", "shared-node", [gpu(0, "NVIDIA RTX A6000", 49152, .idle), gpu(1, "NVIDIA RTX A6000", 49152, .uncertain), gpu(2, "NVIDIA GeForce RTX 3090", 24576, .busy), gpu(3, "NVIDIA GeForce RTX 3090", 24576, .busy)]),
            server("demo-d", "compute-04", (0..<4).map { gpu($0, "NVIDIA Tesla V100-SXM2-32GB", 32768, .unknown) }, offline: true)
        ]
    }
}
