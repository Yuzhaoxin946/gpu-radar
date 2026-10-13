import AppKit
import SwiftUI
import XCTest
@testable import GPURadar

final class MenuLayoutTests: XCTestCase {
    func testHostedServerListHasVisibleHeightAndStopsGrowingAfterTenServers() async {
        await MainActor.run {
            _ = NSApplication.shared
            @MainActor func hostedSize(_ count: Int) -> NSSize {
                let servers = (0..<count).map { index in
                    GPUServer(id: "server-\(index)", name: "server-\(index)", hostname: "host-\(index)",
                              createdAt: Date(), lastSeen: nil, sampledAt: nil, collectionStatus: "pending",
                              error: nil, offline: false, gpus: [])
                }
                let host = NSHostingView(rootView: MenuServerList(servers: servers) { gpu, _ in gpu.state })
                return host.fittingSize
            }
            let one = hostedSize(1)
            let two = hostedSize(2)
            let ten = hostedSize(10)
            let twenty = hostedSize(20)
            XCTAssertGreaterThan(one.height, 30, "The menu's ScrollView must have a nonzero intrinsic height.")
            XCTAssertGreaterThan(two.height, one.height)
            XCTAssertGreaterThan(ten.height, two.height)
            XCTAssertEqual(twenty.height, ten.height, accuracy: 0.5)
            XCTAssertLessThanOrEqual(twenty.height, 360)
            XCTAssertEqual(two.width, 420, accuracy: 0.5)
            XCTAssertEqual(twenty.width, two.width, accuracy: 0.5)
        }
    }
}
