import SwiftUI

private enum Palette {
    static let accent = Color(red: 0.72, green: 0.35, blue: 0.21)
    static let good = Color(red: 0.32, green: 0.56, blue: 0.45)
    static let line = Color.primary.opacity(0.09)
    static let canvas = Color(nsColor: .windowBackgroundColor)
    static let card = Color(nsColor: .controlBackgroundColor)
}

struct DashboardView: View {
    @ObservedObject var store: ReportStore
    @State private var selected: String? = "overview"
    @State private var unavailableExpanded = false

    var body: some View {
        HSplitView {
            sidebar
                .frame(minWidth: 210, idealWidth: 225, maxWidth: 270)
                .frame(maxHeight: .infinity)
                .background(Palette.canvas)

            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    if let report = store.report {
                        if report.demo { notice("Sample data", detail: "A demonstration of the interface. These are not your usage figures.", icon: "sparkles") }
                        if let error = store.errorMessage {
                            notice("Refresh unsuccessful", detail: "\(error) Showing the last successful \(report.period.title.lowercased()) snapshot.", icon: "exclamationmark.triangle")
                        } else if store.isLoading {
                            HStack(spacing: 10) { ProgressView().controlSize(.small); Text("Reading \(store.period.title.lowercased())… The figures below are the last snapshot.").font(.callout).foregroundStyle(.secondary) }
                        }
                        if let source = report.agents.first(where: { $0.id == selected }) {
                            sourceDetail(source, report: report)
                        } else {
                            overview(report)
                        }
                        if !report.notices.isEmpty {
                            VStack(alignment: .leading, spacing: 7) {
                                ForEach(report.notices, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                            }
                        }
                        footer(report)
                    } else {
                        initialState
                    }
                }
                .padding(32)
                .frame(maxWidth: 1200, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .background(Palette.canvas)
            .navigationTitle("")

        }
        .tint(Palette.accent)
        .onAppear { store.refresh() }
        .onChange(of: store.period) { store.refresh() }
        .onChange(of: store.report?.generatedAt) {
            let args = ProcessInfo.processInfo.arguments
            if let index = args.firstIndex(of: "--source"), args.indices.contains(index + 1) { selected = args[index + 1] }
            Snapshot.captureIfRequested()
        }
        .onChange(of: store.errorMessage) { _, value in if value != nil { Snapshot.captureIfRequested() } }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "flame.fill").font(.system(size: 21, weight: .medium)).foregroundStyle(Palette.accent)
                    .frame(width: 37, height: 40).background(Palette.accent.opacity(0.09), in: RoundedRectangle(cornerRadius: 11))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Burnctl").font(.system(size: 20, weight: .semibold, design: .rounded))
                    Text("A clearer view of usage").font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }.padding(.horizontal, 18).padding(.top, 24).padding(.bottom, 27)
            ScrollView {
                VStack(alignment: .leading, spacing: 5) {
                    sidebarButton("Overview", id: "overview", icon: "square.grid.2x2")
                    if let report = store.report {
                        Text("SOURCES").font(.system(size: 9, weight: .semibold)).tracking(1.2).foregroundStyle(.secondary).padding(.top, 24).padding(.leading, 12).padding(.bottom, 6)
                        ForEach(report.available) { source in sidebarRow(source) }
                        if !report.unavailable.isEmpty {
                            Text("NOT AVAILABLE").font(.system(size: 9, weight: .semibold)).tracking(1.2).foregroundStyle(.secondary).padding(.top, 24).padding(.leading, 12).padding(.bottom, 6)
                            ForEach(report.unavailable) { source in sidebarRow(source) }
                        }
                    }
                }.padding(.horizontal, 12).padding(.bottom, 12)
            }
            VStack(alignment: .leading, spacing: 7) {
                Label("Read-only by design", systemImage: "eye").font(.caption.weight(.medium))
                Text("Your sources stay untouched.").font(.caption2).foregroundStyle(.secondary)
            }.foregroundStyle(.secondary).padding(20)
        }
    }

    private func sidebarRow(_ source: UsageSource) -> some View {
        Button { selected = source.id } label: {
            HStack(spacing: 10) {
                Circle().fill(source.isAvailable ? Palette.good : Color.secondary.opacity(0.35)).frame(width: 6, height: 6)
                Text(source.name).font(.system(size: 12, weight: selected == source.id ? .semibold : .regular)).lineLimit(1)
                Spacer(minLength: 0)
            }.padding(.horizontal, 12).padding(.vertical, 10)
                .background(selected == source.id ? Palette.accent.opacity(0.11) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).help("\(source.name) — \(source.statusTitle)")
            .accessibilityAddTraits(selected == source.id ? .isSelected : [])
    }
    private func sidebarButton(_ title: String, id: String, icon: String) -> some View {
        Button { selected = id } label: {
            HStack(spacing: 10) {
                Image(systemName: icon).font(.system(size: 13))
                Text(title).font(.system(size: 12, weight: .semibold))
                Spacer(minLength: 0)
            }.foregroundStyle(selected == id ? Palette.accent : Color.primary)
                .padding(.horizontal, 12).padding(.vertical, 11)
                .background(selected == id ? Palette.accent.opacity(0.11) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityAddTraits(selected == id ? .isSelected : [])
    }

    private func overview(_ report: UsageReport) -> some View {
        VStack(alignment: .leading, spacing: 26) {
            heading(eyebrow: "THE BIG PICTURE", title: "Usage overview", subtitle: "\(report.period.title) · Each source keeps its own reporting window.")
            HStack(alignment: .top, spacing: 14) {
                MetricCard(title: "Estimated API value", value: Display.money(report.total(for: ["estimated"])), note: "Subscription usage at API rates. Not a bill.", accent: true)
                MetricCard(title: "Reported & recorded cost", value: Display.money(report.total(for: ["reported", "recorded"])), note: "Provider and ledger usage. Coverage varies.")
                MetricCard(title: "Sources available", value: "\(report.available.count)", note: "\(report.unavailable.count) unavailable · No data is not zero.")
            }
            VStack(alignment: .leading, spacing: 14) {
                sectionHeading("By source", caption: "Separate measures, not a combined bill")
                if report.available.isEmpty {
                    notice("No usage sources available", detail: "Install or use a supported agent, or configure provider credentials outside this app. Refresh when data is available.", icon: "tray")
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(report.available.enumerated()), id: \.element.id) { index, source in
                            Button { selected = source.id } label: { sourceRow(source) }.buttonStyle(.plain)
                            if index < report.available.count - 1 { Divider().padding(.horizontal, 20) }
                        }
                    }.cardSurface()
                }
            }
            if !report.unavailable.isEmpty {
                DisclosureGroup(isExpanded: $unavailableExpanded) {
                    VStack(spacing: 0) {
                        ForEach(report.unavailable) { source in
                            Button { selected = source.id } label: {
                                HStack(alignment: .top) {
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(source.name).font(.callout.weight(.medium))
                                        Text(source.statusMessage ?? source.statusTitle).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.leading)
                                    }
                                    Spacer()
                                    Text(source.statusTitle).font(.caption).foregroundStyle(.secondary)
                                }.padding(.vertical, 12)
                            }.buttonStyle(.plain)
                        }
                    }.padding(.top, 6)
                } label: {
                    Text("\(report.unavailable.count) unavailable sources").font(.callout.weight(.medium)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func sourceRow(_ source: UsageSource) -> some View {
        HStack(spacing: 16) {
            SourceMark(name: source.name)
            VStack(alignment: .leading, spacing: 6) {
                Text(source.name).font(.system(size: 15, weight: .semibold))
                Text(source.periodText).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 6) {
                Text(source.amountText).font(.system(size: 21, weight: .medium, design: .rounded)).monospacedDigit()
                Text(source.basisTitle).font(.caption).foregroundStyle(.secondary)
            }
            Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary).padding(.leading, 4)
        }.padding(20).contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Show source details")
    }

    private func sourceDetail(_ source: UsageSource, report: UsageReport) -> some View {
        VStack(alignment: .leading, spacing: 26) {
            heading(eyebrow: "SOURCE DETAIL", title: source.name, subtitle: "\(report.period.title) · \(source.periodText)")
            if !source.isAvailable {
                notice(source.statusTitle, detail: source.statusMessage ?? "No readable data was returned for this source. Missing values are not counted as zero.", icon: "tray")
            } else {
                HStack(alignment: .top, spacing: 14) {
                    MetricCard(title: source.basisTitle, value: source.amountText, note: source.basisNote, accent: true)
                    if source.usageUnit == "tokens" {
                        MetricCard(title: "Output tokens", value: Display.compact(source.outputTokens), note: "Generated within this source’s period.")
                        MetricCard(title: "Sessions", value: Display.number(source.sessions), note: "Recorded sessions in the selected period.")
                    }
                }
                if !source.models.isEmpty { modelBreakdown(source) }
                else {
                    notice("No model breakdown", detail: "This source does not provide per-model data for the selected period.", icon: "list.bullet.rectangle")
                }
            }
            VStack(alignment: .leading, spacing: 16) {
                sectionHeading("About these figures", caption: "Context matters")
                VStack(alignment: .leading, spacing: 14) {
                    detailLine("Reporting window", value: source.periodText)
                    Text("Date ranges include the start date and exclude the end date.").font(.caption).foregroundStyle(.secondary)
                    detailLine("Measurement", value: source.basisTitle)
                    if let plan = source.planName, !plan.isEmpty { detailLine("Configured plan", value: plan) }
                    if let activity = source.activityThrough, !activity.isEmpty { detailLine("Activity through", value: Display.date(activity)) }
                    if source.usageUnit == "tokens", source.isAvailable {
                        detailLine("Input tokens", value: Display.number(source.inputTokens))
                        detailLine("Output tokens", value: Display.number(source.outputTokens))
                        detailLine("Messages / requests", value: Display.number(source.messages))
                    }
                    if let message = source.statusMessage, !message.isEmpty {
                        Divider()
                        Text(message).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    }
                }.padding(20).cardSurface()
            }
        }
    }

    private func modelBreakdown(_ source: UsageSource) -> some View {
        let models = source.models
        let hasTokens = models.contains { $0.usage.inputTokens != nil || $0.usage.outputTokens != nil }
        let hasCost = models.contains { $0.usage.cost != nil }
        let maxOutput = models.compactMap { $0.usage.outputTokens }.max() ?? 0
        return VStack(alignment: .leading, spacing: 14) {
            sectionHeading("Model breakdown", caption: hasTokens ? "Token counts · — means not reported" : "Recorded activity · — means not reported")
            VStack(spacing: 0) {
                HStack {
                    Text("MODEL").frame(maxWidth: .infinity, alignment: .leading)
                    if hasTokens {
                        Text("INPUT").frame(width: 95, alignment: .trailing)
                        Text("OUTPUT").frame(width: 95, alignment: .trailing)
                        Text("CACHE¹").frame(width: 95, alignment: .trailing)
                    } else {
                        Text("REQUESTS").frame(width: 95, alignment: .trailing)
                    }
                    if hasCost { Text("COST").frame(width: 90, alignment: .trailing) }
                }.font(.system(size: 10, weight: .semibold)).tracking(0.8).foregroundStyle(.secondary).padding(20)
                Divider()
                ForEach(Array(models.enumerated()), id: \.element.id) { index, model in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 9) {
                            Text(model.id).font(.system(size: 12, weight: .medium, design: .monospaced)).lineLimit(2).textSelection(.enabled)
                            if maxOutput > 0, let output = model.usage.outputTokens {
                                GeometryReader { geometry in
                                    Capsule().fill(Palette.accent.opacity(0.13))
                                        .overlay(alignment: .leading) { Capsule().fill(Palette.accent.opacity(0.65)).frame(width: geometry.size.width * min(1, max(0, output / maxOutput))) }
                                }.frame(width: 115, height: 3).accessibilityHidden(true)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                        if hasTokens {
                            modelNumber(model.usage.inputTokens)
                            modelNumber(model.usage.outputTokens)
                            modelNumber(model.usage.cacheTokens)
                        } else { modelNumber(model.usage.requests) }
                        if hasCost { Text(Display.money(model.usage.cost)).font(.callout).monospacedDigit().frame(width: 90, alignment: .trailing).textSelection(.enabled) }
                    }.padding(.horizontal, 20).padding(.vertical, 17)
                    if index < models.count - 1 { Divider().padding(.horizontal, 20) }
                }
            }.cardSurface()
            if hasTokens {
                Text("¹ Cache counts follow each source’s reporting conventions; they may overlap input tokens. Bars compare model output within this source.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func modelNumber(_ value: Double?) -> some View {
        Text(Display.compact(value)).font(.callout).monospacedDigit().frame(width: 95, alignment: .trailing).help(Display.number(value)).textSelection(.enabled)
    }
    private func detailLine(_ title: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.callout).foregroundStyle(.secondary).frame(width: 160, alignment: .leading)
            Text(value).font(.callout).textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }
    private func heading(eyebrow: String, title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(eyebrow).font(.system(size: 10, weight: .semibold)).tracking(1.9).foregroundStyle(Palette.accent)
            Text(title).font(.system(size: 33, weight: .semibold, design: .rounded)).tracking(-0.7)
            Text(subtitle).font(.callout).foregroundStyle(.secondary)
        }.padding(.top, 6)
    }
    private func sectionHeading(_ title: String, caption: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.system(size: 17, weight: .semibold))
            Spacer()
            Text(caption).font(.caption).foregroundStyle(.secondary)
        }
    }
    private func notice(_ title: String, detail: String, icon: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).foregroundStyle(Palette.accent).padding(.top, 2)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.callout.weight(.semibold))
                Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }.padding(17).frame(maxWidth: .infinity, alignment: .leading).background(Palette.accent.opacity(0.055), in: RoundedRectangle(cornerRadius: 12))
    }
    private func footer(_ report: UsageReport) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            HStack(spacing: 7) {
                Circle().fill(store.isLoading ? Color.secondary : Palette.good).frame(width: 5, height: 5)
                if let date = report.updatedDate {
                    Text("Read \(date.formatted(date: .abbreviated, time: .shortened))").font(.caption)
                } else { Text("Snapshot received").font(.caption) }
                Spacer()
                Text("Read-only · No automatic refresh").font(.caption)
            }.foregroundStyle(.secondary)
            Text("Date ranges include the start date and exclude the end date.").font(.caption2).foregroundStyle(.tertiary)
        }
    }
    private var initialState: some View {
        VStack(alignment: .leading, spacing: 28) {
            heading(eyebrow: "YOUR USAGE, IN FOCUS", title: "Usage overview", subtitle: "A quiet place to read your AI usage.")
            if let error = store.errorMessage {
                notice("Could not read usage", detail: error, icon: "exclamationmark.triangle")
                Button("Try again") { store.refresh() }.buttonStyle(.borderedProminent)
            } else {
                HStack(spacing: 14) {
                    ProgressView().controlSize(.small)
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Reading your sources…").font(.callout.weight(.medium))
                        Text("Large session histories can take a moment. Nothing is being changed.").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(24).frame(maxWidth: .infinity, alignment: .leading).cardSurface()
            }
            Spacer(minLength: 250)
        }
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let note: String
    var accent = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 30, weight: .medium, design: .rounded)).tracking(-0.8).monospacedDigit().foregroundStyle(accent ? Palette.accent : Color.primary).lineLimit(1).minimumScaleFactor(0.65).textSelection(.enabled)
            Text(note).font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).frame(height: 32, alignment: .top)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(20).cardSurface()
    }
}
private struct SourceMark: View {
    let name: String
    var body: some View {
        Text(String(name.prefix(1))).font(.system(size: 17, weight: .semibold, design: .rounded)).foregroundStyle(Palette.accent)
            .frame(width: 39, height: 42).background(Palette.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 11)).accessibilityHidden(true)
    }
}
private extension View {
    func cardSurface() -> some View {
        background(Palette.card, in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(Palette.line, lineWidth: 1))
    }
}
