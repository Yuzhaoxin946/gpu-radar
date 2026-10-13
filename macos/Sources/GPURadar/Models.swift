import Foundation
import SwiftUI

enum GPUState: String, Codable, CaseIterable {
    case idle, uncertain, busy, unknown
    var title: String {
        switch self { case .idle: return "空闲"; case .uncertain: return "被占用"; case .busy: return "使用中"; case .unknown: return "未知" }
    }
    var color: Color {
        switch self {
        case .idle: return Color(red: 0.20, green: 0.69, blue: 0.44)
        case .uncertain: return Color(red: 0.90, green: 0.67, blue: 0.18)
        case .busy: return Color(red: 0.87, green: 0.32, blue: 0.34)
        case .unknown: return Color(red: 0.48, green: 0.52, blue: 0.56)
        }
    }
}

struct GPUProcess: Codable, Identifiable {
    var id: Int { pid }
    let pid: Int
    let username: String?
    let name: String?
    let memoryUsedMib: Double?
}

struct GPU: Codable, Identifiable {
    var id: String { uuid }
    let uuid: String
    let index: Int
    let name: String
    let memoryTotalMib: Double?
    let memoryUsedMib: Double?
    let utilizationGpuPercent: Double?
    let utilizationAvg5MPercent: Double?
    let utilizationWindowSeconds: Double?
    let temperatureCelsius: Double?
    let powerWatts: Double?
    let processesKnown: Bool
    let processes: [GPUProcess]
    let state: GPUState
    let idleSince: Date?
    var freeGiB: Double? {
        guard let total = memoryTotalMib, let used = memoryUsedMib else { return nil }
        return max(0, total - used) / 1024
    }
    var shortName: String { name.replacingOccurrences(of: "NVIDIA ", with: "") }
    var memoryLabel: String {
        guard let used = memoryUsedMib, let total = memoryTotalMib else { return "显存未知" }
        return String(format: "%.1f / %.0f GiB", used / 1024, total / 1024)
    }
    var utilizationLabel: String {
        utilizationGpuPercent.map { String(format: "%.0f%%", $0) } ?? "未知"
    }
    var averageUtilizationLabel: String {
        utilizationAvg5MPercent.map { String(format: "%.1f%%", $0) } ?? "采集中"
    }
    var collectionProgress: String {
        let seconds = Int(min(300, max(0, utilizationWindowSeconds ?? 0)))
        return "\(seconds / 60) 分 \(seconds % 60) 秒 / 5 分钟"
    }
    var hasCompleteWindow: Bool {
        guard let average = utilizationAvg5MPercent, average.isFinite, (0...100).contains(average),
              let seconds = utilizationWindowSeconds, seconds >= 300 else { return false }
        return true
    }
}

struct GPUServer: Codable, Identifiable {
    let id: String
    let name: String
    let hostname: String
    let createdAt: Date
    let lastSeen: Date?
    let sampledAt: Date?
    let collectionStatus: String
    let error: String?
    let offline: Bool
    let gpus: [GPU]
    var modelGroups: [(name: String, gpus: [GPU])] {
        var names: [String] = []
        for gpu in gpus.sorted(by: { $0.index < $1.index }) where !names.contains(gpu.name) { names.append(gpu.name) }
        return names.map { name in (name, gpus.filter { $0.name == name }.sorted { $0.index < $1.index }) }
    }
}

struct Account: Codable { let id: String; let username: String }
struct LoginResponse: Codable { let accessToken: String; let expiresAt: Date; let account: Account }
struct ServersResponse: Codable { let servers: [GPUServer]; let serverTime: Date? }
struct Credentials: Encodable { let username: String; let password: String }
struct RegistrationCredentials: Encodable {
    let username: String
    let password: String
    let inviteCode: String

    init(username: String, password: String, inviteCode: String) {
        self.username = username
        self.password = password
        self.inviteCode = inviteCode.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    enum CodingKeys: String, CodingKey {
        case username, password
        case inviteCode = "invite_code"
    }
}
struct PasswordChangeCredentials: Encodable {
    let currentPassword: String
    let newPassword: String
    enum CodingKeys: String, CodingKey {
        case currentPassword = "current_password"
        case newPassword = "new_password"
    }
}
struct SavedSession: Codable { let baseURL: String; let token: String; let username: String }

enum APIError: LocalizedError {
    case invalidURL, server(Int, String), invalidResponse, keychain(OSStatus)
    var errorDescription: String? {
        switch self {
        case .invalidURL: return "无法连接内置服务端，请更新 GPU Radar。"
        case let .server(code, detail): return "服务端返回 \(code)：\(detail)"
        case .invalidResponse: return "服务端响应格式不正确。"
        case .keychain: return "无法安全访问钥匙串，请检查 macOS 钥匙串权限。"
        }
    }
    var unauthorized: Bool { if case .server(401, _) = self { return true }; return false }
}

enum API {
    static let baseURL = "https://123.56.135.166"
    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.timeoutIntervalForResource = 20
        return URLSession(configuration: configuration, delegate: SameOriginRedirectDelegate(), delegateQueue: nil)
    }()
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let string = try decoder.singleValueContainer().decode(String.self)
            let parser = ISO8601DateFormatter()
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = parser.date(from: string) { return date }
            parser.formatOptions = [.withInternetDateTime]
            if let date = parser.date(from: string) { return date }
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid ISO-8601 timestamp"))
        }
        return decoder
    }()

    static func isBuiltInServer(_ savedURL: String) -> Bool {
        guard let components = URLComponents(string: savedURL),
              components.scheme == "https", components.host == "123.56.135.166",
              components.port == nil || components.port == 443,
              components.path.isEmpty || components.path == "/",
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil else { return false }
        return true
    }

    static func request(path: String, method: String = "GET", token: String? = nil, body: Data? = nil) async throws -> Data {
        guard let url = URL(string: baseURL + "/api/v1" + path) else { throw APIError.invalidURL }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 12)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200...299).contains(http.statusCode) else {
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let detail = (json?["detail"] as? String) ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIError.server(http.statusCode, detail)
        }
        return data
    }
}

private final class SameOriginRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let original = task.originalRequest?.url, let destination = request.url,
              original.scheme == destination.scheme,
              original.host == destination.host,
              original.port == destination.port else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}
