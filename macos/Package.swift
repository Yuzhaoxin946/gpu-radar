// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GPURadar",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "GPURadar", targets: ["GPURadar"])],
    targets: [
        .executableTarget(name: "GPURadar"),
        .testTarget(name: "GPURadarTests", dependencies: ["GPURadar"])
    ]
)
