import SwiftUI

struct RollDetailView: View {
    @EnvironmentObject private var store: RollStore
    let rollID: UUID
    var developedOnly = false
    private let columns = [GridItem(.adaptive(minimum: 105), spacing: 2)]

    var roll: FilmRoll? { store.rolls.first { $0.id == rollID } }

    var body: some View {
        ScrollView {
            if let roll {
                LazyVGrid(columns: columns, spacing: 2) {
                    ForEach(developedOnly ? roll.frames.filter(\.isDeveloped) : roll.frames) { frame in
                        NavigationLink {
                            FrameEditorView(rollID: roll.id, frameID: frame.id)
                        } label: {
                            MediaPreview(
                                url: (developedOnly ? store.developedURL(rollID: roll.id, frame: frame) : nil)
                                    ?? store.frameURL(rollID: roll.id, frame: frame),
                                frame: frame,
                                isDevelopedJPEG: developedOnly
                            )
                                .aspectRatio(1, contentMode: .fill).clipped()
                        }
                    }
                }
            }
        }
        .navigationTitle(roll?.name ?? "Roll")
    }
}

struct MediaPreview: View {
    let url: URL
    let frame: RollFrame
    var isDevelopedJPEG = false
    @EnvironmentObject private var catalog: PresetCatalog
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFill() }
            else { Rectangle().fill(.quaternary).overlay { ProgressView() } }
        }
        .task(id: "\(frame.presetID)-\(frame.intensity)") {
            if isDevelopedJPEG, let loaded = UIImage(contentsOfFile: url.path) {
                image = loaded
                return
            }
            guard let preset = catalog.preset(id: frame.presetID) else { return }
            image = try? await Task.detached {
                try One35Processor.shared.render(url: url, preset: preset, intensity: frame.intensity, maxDimension: 420)
            }.value
        }
    }
}
