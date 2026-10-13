import XCTest
@testable import GPURadar

final class ContractTests: XCTestCase {
    func testFiveMinuteAverageDecodesSnakeCaseWithDigit() throws {
        let gpu = try decodeGPU(average: "7.5", window: "300")
        XCTAssertEqual(gpu.utilizationAvg5MPercent, 7.5)
        XCTAssertEqual(gpu.utilizationWindowSeconds, 300)
        XCTAssertTrue(gpu.hasCompleteWindow)
    }

    func testIncompleteOrMissingWindowCannotBeTrusted() throws {
        XCTAssertFalse(try decodeGPU(average: "0", window: "299.9").hasCompleteWindow)
        XCTAssertFalse(try decodeGPU(average: "null", window: "300").hasCompleteWindow)
        XCTAssertFalse(try decodeGPU(average: "0", window: "null").hasCompleteWindow)
    }

    func testPasswordRequestUsesAPIFieldNames() throws {
        let body = try JSONEncoder().encode(PasswordChangeCredentials(currentPassword: "old-example", newPassword: "new-example"))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(object, ["current_password": "old-example", "new_password": "new-example"])
    }

    func testOnlyBuiltInServerCanRestoreSavedSession() {
        XCTAssertEqual(API.baseURL, "https://123.56.135.166")
        XCTAssertTrue(API.isBuiltInServer("https://123.56.135.166"))
        XCTAssertTrue(API.isBuiltInServer("https://123.56.135.166:443/"))
        for url in ["http://123.56.135.166", "http://127.0.0.1:8000", "https://example.com", "https://123.56.135.166:8443", "https://123.56.135.166/other", "https://user@123.56.135.166", "https://123.56.135.166?override=true"] {
            XCTAssertFalse(API.isBuiltInServer(url), url)
        }
    }

    private func decodeGPU(average: String, window: String) throws -> GPU {
        let json = """
        {"uuid":"GPU-contract", "index":7, "name":"NVIDIA A100", "memory_total_mib":81920,
         "memory_used_mib":100, "utilization_gpu_percent":2,
         "utilization_avg_5m_percent":\(average), "utilization_window_seconds":\(window),
         "temperature_celsius":40, "power_watts":50, "processes_known":true,
         "processes":[], "state":"idle", "idle_since":null}
        """
        return try API.decoder.decode(GPU.self, from: Data(json.utf8))
    }
}
