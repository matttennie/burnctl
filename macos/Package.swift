// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Burnctl",
    platforms: [.macOS("15.0")],
    products: [.executable(name: "Burnctl", targets: ["Burnctl"])],
    targets: [
        .executableTarget(name: "Burnctl", path: "Sources/Burnctl"),
        .testTarget(name: "BurnctlTests", dependencies: ["Burnctl"], path: "Tests/BurnctlTests"),
    ]
)
