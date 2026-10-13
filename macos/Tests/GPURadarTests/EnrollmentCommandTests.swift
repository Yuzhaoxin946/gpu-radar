import XCTest
@testable import GPURadar

final class EnrollmentCommandTests: XCTestCase {
    func testDefaultInstallUsesCurrentAccountAndHostDefaultName() {
        let commands = EnrollmentCommands(username: "researcher", serverName: "  ")
        let command = commands.install(from: .github)
        XCTAssertEqual(command, "curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username 'researcher'")
        XCTAssertFalse(command.contains("--name"))
        XCTAssertFalse(command.contains("--password"))
    }

    func testMirrorChangesBothDownloadAndInstallerOption() {
        let commands = EnrollmentCommands(username: "lab-user", serverName: "  GPU lab  ")
        XCTAssertEqual(commands.install(from: .aliyun), "curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username 'lab-user' --name 'GPU lab'")
    }

    func testShellQuotedNamePreservesQuotesAndDoesNotExpandShellSyntax() throws {
        let name = "lab's $(printf unsafe) `printf unsafe` $USER; GPU"
        let commands = EnrollmentCommands(username: "researcher", serverName: name)
        let command = commands.install(from: .github)
        let marker = try XCTUnwrap(command.range(of: " --name "))
        let result = try shell("printf '%s' " + String(command[marker.upperBound...]))
        XCTAssertEqual(result, name)
        _ = try shell(commands.login, syntaxOnly: true)
    }

    func testLoginUsesTheInstallerHostPathsAndExplicitConfiguration() {
        let command = EnrollmentCommands(username: "lab-user", serverName: "GPU-02").login
        XCTAssertTrue(command.contains("cat /etc/machine-id"))
        XCTAssertTrue(command.contains("re.fullmatch(r\"[0-9a-f]{32}\""))
        XCTAssertTrue(command.contains("sys.argv[1] != \"0\" * 32"))
        XCTAssertTrue(command.contains("$HOME/.local/share/gpu-radar/hosts/$machine_id/gpu-radar-agent.pyz\" login"))
        XCTAssertTrue(command.contains("--config \"$HOME/.config/gpu-radar/hosts/$machine_id/agent.json\""))
        XCTAssertTrue(command.contains("--username 'lab-user' --name 'GPU-02'"))
        XCTAssertFalse(command.contains("logout"))
        XCTAssertFalse(command.contains("--password"))
    }

    func testInvalidNamesCannotBeOfferedForCopy() {
        XCTAssertFalse(EnrollmentCommands(username: "researcher", serverName: String(repeating: "x", count: 81)).nameIsValid)
        XCTAssertFalse(EnrollmentCommands(username: "researcher", serverName: "gpu\nserver").nameIsValid)
        XCTAssertTrue(EnrollmentCommands(username: "researcher", serverName: "").nameIsValid)
    }

    private func shell(_ command: String, syntaxOnly: Bool = false) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = syntaxOnly ? ["-n", "-c", command] : ["-c", command]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let result = String(decoding: data, as: UTF8.self)
        XCTAssertEqual(process.terminationStatus, 0, result)
        return result
    }
}
