import Foundation

enum ReportPeriod: String, CaseIterable, Identifiable, Codable, Sendable {
    case current, previous
    var id: String { rawValue }
    var title: String { self == .current ? "Current period" : "Previous period" }
}

struct UsageReport: Decodable, Sendable {
    let schemaVersion: Int
    let generatedAt: String
    let period: ReportPeriod
    let demo: Bool
    let agents: [UsageSource]
    let notices: [String]

    static func decode(_ data: Data) throws -> UsageReport {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let report = try decoder.decode(Self.self, from: data)
        guard report.schemaVersion == 1 else { throw ReportError.unsupportedVersion }
        return report
    }
    var available: [UsageSource] { agents.filter(\.isAvailable) }
    var unavailable: [UsageSource] { agents.filter { !$0.isAvailable } }
    func total(for bases: Set<String>) -> Double? {
        let amounts = available.filter { bases.contains($0.costBasis) }.compactMap(\.periodCost)
        return amounts.isEmpty ? nil : amounts.reduce(0, +)
    }
    var updatedDate: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: generatedAt) ?? ISO8601DateFormatter().date(from: generatedAt)
    }
}

struct UsageSource: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let sourceStatus: String
    let statusMessage: String?
    let costBasis: String
    let usageUnit: String
    let usageCount: Double?
    let periodCost: Double?
    let inputTokens: Double?
    let outputTokens: Double?
    let messages: Double?
    let sessions: Double?
    let planName: String?
    let planPrice: Double?
    let interval: String?
    let periodStart: String?
    let periodEnd: String?
    let activityThrough: String?
    let lastActive: String?
    let rollingWindow: Int?
    let modelUsage: [String: ModelUsage]?

    var isAvailable: Bool { sourceStatus == "ok" }
    var isQuota: Bool { costBasis == "quota" }
    var isEstimated: Bool { costBasis == "estimated" }
    var basisTitle: String {
        switch costBasis {
        case "estimated": "Estimated API value"
        case "reported": "Provider-reported cost"
        case "recorded": "Recorded usage cost"
        case "quota": usageUnit == "characters" ? "Characters used" : "Credits used"
        default: "Usage"
        }
    }
    var basisNote: String {
        switch costBasis {
        case "estimated": "API-equivalent estimate. This is not your subscription bill."
        case "reported": "Usage reported by the provider. This is not an invoice."
        case "recorded": "Cost recorded in local usage data; coverage may be partial."
        case "quota": "Provider-reported quota usage, not a dollar amount."
        default: "This source does not report a usage cost."
        }
    }
    var amountText: String {
        guard isAvailable else { return "—" }
        return isQuota ? Display.number(usageCount) : Display.money(periodCost)
    }
    var periodText: String {
        guard let start = periodStart, let end = periodEnd else { return "Period unavailable" }
        return "\(Display.date(start)) – \(Display.date(end))"
    }
    var statusTitle: String {
        switch sourceStatus {
        case "ok": "Available"
        case "unsupported": "Not supported"
        case "error": "Could not read"
        default: "Unavailable"
        }
    }
    var models: [ModelRow] {
        (modelUsage ?? [:]).map { ModelRow(id: $0.key, usage: $0.value) }
            .sorted { lhs, rhs in
                let a = lhs.usage.outputTokens ?? lhs.usage.cost ?? 0
                let b = rhs.usage.outputTokens ?? rhs.usage.cost ?? 0
                return a == b ? lhs.id < rhs.id : a > b
            }
    }
}

struct ModelRow: Identifiable, Sendable {
    let id: String
    let usage: ModelUsage
}
struct ModelUsage: Decodable, Sendable {
    let inputTokens: Double?
    let outputTokens: Double?
    let cacheReadInputTokens: Double?
    let cacheCreationInputTokens: Double?
    let cachedTokens: Double?
    let cost: Double?
    let requests: Double?
    var cacheTokens: Double? {
        let values = [cacheReadInputTokens, cacheCreationInputTokens, cachedTokens].compactMap { $0 }
        return values.isEmpty ? nil : values.reduce(0, +)
    }
}

enum Display {
    static func money(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.currency(code: "USD").locale(Locale(identifier: "en_US")))
    }
    static func number(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        return value.formatted(.number.precision(.fractionLength(0)).locale(Locale(identifier: "en_US")))
    }
    static func compact(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        let unit: (Double, String) = value >= 1_000_000_000 ? (1_000_000_000, "B") : value >= 1_000_000 ? (1_000_000, "M") : value >= 10_000 ? (1_000, "K") : (1, "")
        return (value / unit.0).formatted(.number.precision(.fractionLength(unit.0 == 1 ? 0 : 1)).locale(Locale(identifier: "en_US"))) + unit.1
    }
    static func date(_ string: String) -> String {
        let input = DateFormatter()
        input.locale = Locale(identifier: "en_US_POSIX")
        input.timeZone = TimeZone(secondsFromGMT: 0)
        input.dateFormat = "yyyy-MM-dd"
        guard let date = input.date(from: String(string.prefix(10))) else { return string }
        let output = DateFormatter()
        output.locale = Locale(identifier: "en_US")
        output.timeZone = input.timeZone
        output.dateFormat = "MMM d, yyyy"
        return output.string(from: date)
    }
}

enum ReportError: LocalizedError {
    case missingEngine, unsupportedVersion, timedOut, failed, invalidResponse
    var errorDescription: String? {
        switch self {
        case .missingEngine: "The bundled reporting engine is missing. Please rebuild or replace this copy of Burnctl."
        case .unsupportedVersion: "This version of the app cannot read the reporting engine’s data format."
        case .timedOut: "Reading usage took too long. You can refresh to try again."
        case .failed: "The reporting engine could not finish. Your source files have not been changed."
        case .invalidResponse: "The reporting engine returned data that this app could not read."
        }
    }
}
