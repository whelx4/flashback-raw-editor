import SwiftUI

struct FrameEditorView: View {
    @EnvironmentObject private var store: RollStore
    @EnvironmentObject private var catalog: PresetCatalog
    let rollID: UUID
    let frameID: UUID
    @State private var preview: UIImage?
    @State private var beforePreview: UIImage?
    @State private var rendering = false
    @State private var developing = false
    @State private var developedURL: URL?
    @State private var exportError: String?
    @GestureState private var comparing = false

    private var location: (roll: Int, frame: Int)? {
        guard let rollIndex = store.rolls.firstIndex(where: { $0.id == rollID }),
              let frameIndex = store.rolls[rollIndex].frames.firstIndex(where: { $0.id == frameID })
        else { return nil }
        return (rollIndex, frameIndex)
    }

    var body: some View {
        Group {
            if let location {
                let roll = store.rolls[location.roll]
                let frame = roll.frames[location.frame]
                VStack(spacing: 18) {
                    ZStack {
                        Color.black
                        if let shown = comparing ? (beforePreview ?? preview) : preview {
                            Image(uiImage: shown).resizable().scaledToFit()
                        }
                        if rendering { ProgressView().tint(.white) }
                        if comparing {
                            Text("BEFORE").font(.caption.bold()).padding(7)
                                .background(.black.opacity(0.7)).foregroundStyle(.white)
                                .clipShape(Capsule()).frame(maxWidth: .infinity, maxHeight: .infinity,
                                                           alignment: .topLeading).padding()
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .gesture(LongPressGesture(minimumDuration: 0.05)
                        .updating($comparing) { value, state, _ in state = value })

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack {
                            ForEach(catalog.presets) { preset in
                                Button {
                                    edit { $0.presetID = preset.id }
                                } label: {
                                    Text(preset.name)
                                        .font(.caption.bold()).padding(.horizontal, 12).padding(.vertical, 9)
                                        .background(frame.presetID == preset.id ? Color.orange : Color.secondary.opacity(0.15))
                                        .foregroundStyle(frame.presetID == preset.id ? .white : .primary)
                                        .clipShape(Capsule())
                                }
                            }
                        }.padding(.horizontal)
                    }

                    VStack {
                        HStack { Text("FILTER INTENSITY"); Spacer(); Text("\(Int(frame.intensity * 100))%") }
                            .font(.caption.monospacedDigit())
                        Slider(value: Binding(
                            get: { frame.intensity },
                            set: { value in edit { $0.intensity = value } }
                        ), in: 0...1)
                        .tint(.orange)
                    }.padding(.horizontal)

                    HStack {
                        Button {
                            Task { await develop(roll: roll, frame: frame) }
                        } label: {
                            Label(developing ? "Developing…" : "Develop JPEG",
                                  systemImage: "wand.and.stars")
                        }
                        .buttonStyle(.borderedProminent).tint(.orange)
                        .disabled(developing)

                        if let developedURL {
                            ShareLink(item: developedURL) {
                                Label("Share", systemImage: "square.and.arrow.up")
                            }.buttonStyle(.bordered)
                        }
                    }.padding(.horizontal)
                }
                .task(id: "\(frame.presetID)-\(frame.intensity)") { await render(roll: roll, frame: frame) }
            } else {
                ContentUnavailableView("Frame unavailable", systemImage: "exclamationmark.triangle")
            }
        }
        .background(Color.black.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .alert("Development failed", isPresented: Binding(
            get: { exportError != nil }, set: { if !$0 { exportError = nil } }
        )) { Button("OK", role: .cancel) {} } message: { Text(exportError ?? "Unknown error") }
    }

    private func edit(_ mutation: (inout RollFrame) -> Void) {
        guard let location else { return }
        var roll = store.rolls[location.roll]
        mutation(&roll.frames[location.frame])
        store.update(roll: roll)
    }

    private func render(roll: FilmRoll, frame: RollFrame) async {
        guard let preset = catalog.preset(id: frame.presetID) else { return }
        rendering = true
        let url = store.frameURL(rollID: roll.id, frame: frame)
        let renderTask = Task.detached {
            let edited = try One35Processor.shared.render(
                url: url, preset: preset, intensity: frame.intensity)
            let neutral = try One35Processor.shared.render(
                url: url, preset: preset, intensity: 0)
            return (edited, neutral)
        }
        if let pair = try? await renderTask.value, !Task.isCancelled {
            preview = pair.0
            beforePreview = pair.1
        }
        rendering = false
        developedURL = store.developedURL(rollID: roll.id, frame: frame)
    }

    private func develop(roll: FilmRoll, frame: RollFrame) async {
        guard let preset = catalog.preset(id: frame.presetID) else { return }
        developing = true
        defer { developing = false }
        do {
            let source = store.frameURL(rollID: roll.id, frame: frame)
            let image = try await Task.detached {
                try One35Processor.shared.render(
                    url: source, preset: preset, intensity: frame.intensity,
                    maxDimension: 6000)
            }.value
            guard let data = image.jpegData(compressionQuality: 0.95) else {
                throw One35Processor.RenderError.cannotRender
            }
            developedURL = try store.saveDevelopedJPEG(
                data, rollID: roll.id, frameID: frame.id, suffix: preset.exportSuffix)
        } catch {
            exportError = error.localizedDescription
        }
    }
}
