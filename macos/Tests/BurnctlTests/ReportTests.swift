import XCTest
@testable import Burnctl

final class ReportTests: XCTestCase {
    func testFixtureDecodesAndKeepsCostBasesSeparate() throws {
        let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Fixtures/report.json")
        let report = try UsageReport.decode(Data(contentsOf: fixture))
        XCTAssertTrue(report.demo)
        XCTAssertFalse(report.available.isEmpty)
        let estimated = report.available.filter { $0.costBasis == "estimated" }.compactMap(\.periodCost).reduce(0, +)
        XCTAssertEqual(report.total(for: ["estimated"]), estimated)
        XCTAssertFalse(report.available.contains { $0.sourceStatus != "ok" })
    }
    func testMissingAmountsAreNotZero() throws {
        let report = try sampleReport(extra: "{\"id\":\"missing\",\"name\":\"Missing\",\"source_status\":\"unavailable\",\"cost_basis\":\"estimated\",\"usage_unit\":\"tokens\",\"period_cost\":100}")
        XCTAssertNil(report.total(for: ["estimated"]))
        XCTAssertEqual(report.agents[0].amountText, "—")
        XCTAssertEqual(Display.money(nil), "—")
        XCTAssertEqual(Display.money(0), "$0.00")
        XCTAssertEqual(Display.number(nil), "—")
        XCTAssertEqual(Display.number(0), "0")
    }
    func testUnsupportedVersionRejected() {
        XCTAssertThrowsError(try UsageReport.decode(Data("{\"schema_version\":2,\"generated_at\":\"now\",\"period\":\"current\",\"demo\":false,\"agents\":[],\"notices\":[]}".utf8)))
    }
    func testModelWithCostsDoesNotInventTokens() throws {
        let report = try sampleReport(extra: "{\"id\":\"hf\",\"name\":\"HF\",\"source_status\":\"ok\",\"cost_basis\":\"reported\",\"usage_unit\":\"tokens\",\"model_usage\":{\"provider\":{\"cost\":1.25,\"requests\":2}}}")
        let model = try XCTUnwrap(report.agents.first?.models.first?.usage)
        XCTAssertNil(model.outputTokens)
        XCTAssertNil(model.cacheTokens)
        XCTAssertEqual(model.cost, 1.25)
    }
    func testModelCacheAndCamelCaseKeysDecode() throws {
        let report = try sampleReport(extra: "{\"id\":\"codex\",\"name\":\"Codex\",\"source_status\":\"ok\",\"cost_basis\":\"estimated\",\"usage_unit\":\"tokens\",\"model_usage\":{\"model\":{\"inputTokens\":120,\"outputTokens\":30,\"cacheReadInputTokens\":90}}}")
        let model = try XCTUnwrap(report.agents.first?.models.first?.usage)
        XCTAssertEqual(model.inputTokens, 120)
        XCTAssertEqual(model.outputTokens, 30)
        XCTAssertEqual(model.cacheTokens, 90)
    }
    func testQuotaUsesCountNotMoney() throws {
        let report = try sampleReport(extra: "{\"id\":\"voice\",\"name\":\"Voice\",\"source_status\":\"ok\",\"cost_basis\":\"quota\",\"usage_unit\":\"characters\",\"usage_count\":42000,\"period_cost\":null}")
        XCTAssertEqual(report.agents[0].amountText, "42,000")
        XCTAssertNil(report.total(for: ["reported", "estimated", "recorded"]))
    }
    func testDateAndNumberFormatting() {
        XCTAssertEqual(Display.date("2026-11-01"), "Nov 1, 2026")
        XCTAssertEqual(Display.compact(1_200_000), "1.2M")
        XCTAssertEqual(Display.compact(.infinity), "—")
    }
    func testMissingEngineIsActionable() async {
        do { _ = try await EngineClient(executable: URL(fileURLWithPath: "/does/not/exist")).load(period: .current, demo: false); XCTFail("Expected missing helper error") }
        catch { XCTAssertTrue(error.localizedDescription.contains("missing")) }
    }
    func testNonzeroEngineExitIsSanitized() async {
        do { _ = try await EngineClient(executable: URL(fileURLWithPath: "/usr/bin/false")).load(period: .current, demo: false); XCTFail("Expected failure") }
        catch { XCTAssertTrue(error.localizedDescription.contains("could not finish")) }
    }
}

private func sampleReport(period: ReportPeriod = .current, extra: String = "") throws -> UsageReport {
    try UsageReport.decode(Data("{\"schema_version\":1,\"generated_at\":\"2026-11-01T12:00:00+00:00\",\"period\":\"\(period.rawValue)\",\"demo\":false,\"agents\":[\(extra)],\"notices\":[]}".utf8))
}

private actor ControlledLoader: ReportLoading {
    var waiting: [(ReportPeriod, CheckedContinuation<UsageReport, Error>)] = []
    func load(period: ReportPeriod, demo: Bool) async throws -> UsageReport {
        try await withCheckedThrowingContinuation { waiting.append((period, $0)) }
    }
    var count: Int { waiting.count }
    func succeed(at index: Int, period: ReportPeriod) throws { waiting[index].1.resume(returning: try sampleReport(period: period)) }
    func fail(at index: Int) { waiting[index].1.resume(throwing: ReportError.failed) }
}

final class ReportStoreTests: XCTestCase {
    @MainActor
    private func waitFor(_ predicate: @escaping @MainActor () async -> Bool) async throws {
        for _ in 0..<200 {
            if await predicate() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Timed out waiting for state")
    }
    @MainActor
    func testRapidPeriodSwitchRejectsStaleCompletion() async throws {
        let loader = ControlledLoader()
        let store = ReportStore(loader: loader)
        store.refresh()
        try await waitFor { await loader.count == 1 }
        store.period = .previous
        store.refresh()
        try await waitFor { await loader.count == 2 }
        try await loader.succeed(at: 1, period: .previous)
        try await waitFor { !store.isLoading }
        try await loader.succeed(at: 0, period: .current)
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(store.report?.period, .previous)
    }
    @MainActor
    func testFailedRefreshPreservesPriorSnapshotAndExposesError() async throws {
        let loader = ControlledLoader()
        let store = ReportStore(loader: loader)
        store.refresh()
        try await waitFor { await loader.count == 1 }
        try await loader.succeed(at: 0, period: .current)
        try await waitFor { !store.isLoading }
        store.period = .previous
        store.refresh()
        try await waitFor { await loader.count == 2 }
        await loader.fail(at: 1)
        try await waitFor { !store.isLoading }
        XCTAssertEqual(store.report?.period, .current)
        XCTAssertEqual(store.period, .previous)
        XCTAssertNotNil(store.errorMessage)
    }
    @MainActor
    func testWrongPeriodResponseIsRejected() async throws {
        let loader = ControlledLoader()
        let store = ReportStore(loader: loader)
        store.refresh()
        try await waitFor { await loader.count == 1 }
        try await loader.succeed(at: 0, period: .previous)
        try await waitFor { !store.isLoading }
        XCTAssertNil(store.report)
        XCTAssertNotNil(store.errorMessage)
    }
}


final class EngineJobTests: XCTestCase {
    func testTimeoutTerminatesAndReapsOwnedProcess() throws {
        let job = EngineJob(executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["10"], timeout: 0.05)
        let start = Date()
        XCTAssertThrowsError(try job.run()) { error in
            XCTAssertTrue(error.localizedDescription.contains("too long"))
        }
        XCTAssertFalse(job.process.isRunning)
        XCTAssertLessThan(Date().timeIntervalSince(start), 3)
    }
    func testCancellationTerminatesAndReapsOwnedProcess() async throws {
        let job = EngineJob(executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["10"], timeout: 20)
        let task = Task.detached { try job.run() }
        for _ in 0..<200 {
            if job.process.isRunning { break }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertTrue(job.process.isRunning)
        job.cancel()
        do { _ = try await task.value; XCTFail("Expected cancellation") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertFalse(job.process.isRunning)
    }
    func testStderrLargerThanPipeCapacityDoesNotDeadlock() throws {
        let job = EngineJob(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "/bin/dd if=/dev/zero bs=65536 count=8 1>&2; printf done"], timeout: 5)
        let data = try job.run()
        XCTAssertEqual(String(data: data, encoding: .utf8), "done")
        XCTAssertFalse(job.process.isRunning)
    }
}
