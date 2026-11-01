import Foundation
import Combine
import Darwin

protocol ReportLoading: Sendable {
    func load(period: ReportPeriod, demo: Bool) async throws -> UsageReport
}

struct EngineClient: ReportLoading {
    let executable: URL
    var timeout: TimeInterval = 120
    init(executable: URL = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/burnctl-engine"), timeout: TimeInterval = 120) {
        self.executable = executable
        self.timeout = timeout
    }
    func load(period: ReportPeriod, demo: Bool) async throws -> UsageReport {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { throw ReportError.missingEngine }
        let job = EngineJob(executable: executable, arguments: ["--period", period.rawValue] + (demo ? ["--demo"] : []), timeout: timeout)
        let data = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .userInitiated).async {
                    continuation.resume(with: Result { try job.run() })
                }
            }
        } onCancel: {
            job.cancel()
        }
        try Task.checkCancellation()
        do { return try UsageReport.decode(data) }
        catch let error as ReportError { throw error }
        catch { throw ReportError.invalidResponse }
    }
}

/// Owns just one helper process. Both output streams are drained concurrently so
/// neither a large report nor diagnostics can deadlock the child.
final class EngineJob: @unchecked Sendable {
    let process = Process()
    let timeout: TimeInterval
    let lock = NSLock()
    var cancelled = false
    var expired = false
    init(executable: URL, arguments: [String], timeout: TimeInterval) {
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        self.timeout = timeout
    }
    func cancel() {
        lock.lock()
        cancelled = true
        stopLocked()
        lock.unlock()
    }
    private func stopLocked() {
        guard process.isRunning else { return }
        process.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [self] in
            lock.lock()
            defer { lock.unlock() }
            if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
        }
    }
    func run() throws -> Data {
        let output = Pipe(), errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        lock.lock()
        if cancelled { lock.unlock(); throw CancellationError() }
        do { try process.run() } catch { lock.unlock(); throw ReportError.failed }
        lock.unlock()
        let timer = DispatchWorkItem { [self] in
            lock.lock()
            defer { lock.unlock() }
            if process.isRunning {
                expired = true
                stopLocked()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: timer)
        let readers = DispatchGroup()
        readers.enter()
        DispatchQueue.global().async {
            // Never surface raw diagnostics: provider errors may include credentials.
            while !(errors.fileHandleForReading.availableData.isEmpty) {}
            readers.leave()
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        readers.wait()
        timer.cancel()
        lock.lock()
        let wasCancelled = cancelled, wasExpired = expired
        lock.unlock()
        if wasCancelled { throw CancellationError() }
        if wasExpired { throw ReportError.timedOut }
        guard process.terminationStatus == 0 else { throw ReportError.failed }
        return data
    }
}

@MainActor
final class ReportStore: ObservableObject {
    @Published var period: ReportPeriod = .current
    @Published private(set) var report: UsageReport?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    let demo: Bool
    private let loader: any ReportLoading
    private var task: Task<Void, Never>?
    private var requestID = UUID()

    init(demo: Bool = false, loader: any ReportLoading = EngineClient()) {
        self.demo = demo
        self.loader = loader
    }
    func refresh() {
        task?.cancel()
        let id = UUID()
        requestID = id
        let requestedPeriod = period
        isLoading = true
        errorMessage = nil
        task = Task { [weak self, loader, demo] in
            do {
                let next = try await loader.load(period: requestedPeriod, demo: demo)
                guard let self, self.requestID == id, !Task.isCancelled else { return }
                guard next.period == requestedPeriod else { throw ReportError.invalidResponse }
                self.report = next
                self.isLoading = false
            } catch {
                guard let self, self.requestID == id, !Task.isCancelled else { return }
                self.errorMessage = (error as? LocalizedError)?.errorDescription ?? "Usage could not be read. Please try refreshing."
                self.isLoading = false
            }
        }
    }
    func cancel() {
        requestID = UUID()
        task?.cancel()
        task = nil
        isLoading = false
    }
}
