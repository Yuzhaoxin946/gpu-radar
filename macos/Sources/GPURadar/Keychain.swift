import Foundation
import Security

enum SessionKeychain {
    // Legacy macOS keychain APIs are synchronous and may wait for SecurityAgent.
    // Keep every operation off the UI thread and serialize the interaction flag.
    private static let queue = DispatchQueue(label: "app.gpuradar.keychain", qos: .userInitiated)
    private static let service = "app.gpuradar.account-session"
    private static let account = "current"
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    static func save(_ session: SavedSession) async throws {
        try await perform { try saveSynchronously(session) }
    }
    static func load() async throws -> SavedSession? {
        try await perform { try withoutInteraction { try loadSynchronously() } }
    }
    static func delete() async throws {
        try await perform { try withoutInteraction { try deleteSynchronously() } }
    }
    private static func perform<Value>(_ action: @escaping () throws -> Value) async throws -> Value {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                do { continuation.resume(returning: try action()) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }
    private static func withoutInteraction<Value>(_ action: () throws -> Value) throws -> Value {
        // This app uses the file-based keychain so it can run without a developer
        // entitlement. LAContext.interactionNotAllowed does not cover its ACL
        // prompts. These legacy controls are required for that keychain backend.
        var previous = DarwinBoolean(true)
        let readStatus = SecKeychainGetUserInteractionAllowed(&previous)
        guard readStatus == errSecSuccess else { throw APIError.keychain(readStatus) }
        let changeStatus = SecKeychainSetUserInteractionAllowed(false)
        guard changeStatus == errSecSuccess else { throw APIError.keychain(changeStatus) }
        defer { SecKeychainSetUserInteractionAllowed(previous.boolValue) }
        return try action()
    }
    private static func saveSynchronously(_ session: SavedSession) throws {
        let data = try JSONEncoder().encode(session)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let status = SecItemAdd(item as CFDictionary, nil)
            guard status == errSecSuccess else { throw APIError.keychain(status) }
        } else if update != errSecSuccess { throw APIError.keychain(update) }
    }
    private static func loadSynchronously() throws -> SavedSession? {
        var item = query
        item[kSecReturnData as String] = true
        item[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(item as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw APIError.keychain(status) }
        return try JSONDecoder().decode(SavedSession.self, from: data)
    }
    private static func deleteSynchronously() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw APIError.keychain(status) }
    }
}
