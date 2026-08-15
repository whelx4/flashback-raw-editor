import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var store: RollStore
    @State private var importing = false

    private var importTypes: [UTType] {
        let extensions = [
            "dng", "arw", "nef", "nrw", "cr2", "cr3", "raf", "orf", "rw2",
            "pef", "srw", "raw", "jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp"
        ]
        return [.folder] + extensions.compactMap { UTType(filenameExtension: $0) }
    }

    var body: some View {
        TabView {
            NavigationStack { CameraImportView(importing: $importing) }
                .tabItem { Label("My Camera", systemImage: "camera") }
            NavigationStack { RollListView(title: "Photo Lab", developedOnly: false) }
                .tabItem { Label("Photo Lab", systemImage: "flask") }
            NavigationStack { RollListView(title: "Gallery", developedOnly: true) }
                .tabItem { Label("Gallery", systemImage: "photo.on.rectangle") }
        }
        .tint(.orange)
        .fileImporter(
            isPresented: $importing,
            allowedContentTypes: importTypes,
            allowsMultipleSelection: true
        ) { result in
            guard case .success(let urls) = result else { return }
            Task { await store.importMedia(urls) }
        }
        .alert("Import failed", isPresented: Binding(
            get: { store.importError != nil },
            set: { if !$0 { store.importError = nil } }
        )) { Button("OK", role: .cancel) {} } message: {
            Text(store.importError ?? "Unknown error")
        }
    }
}

private struct CameraImportView: View {
    @Binding var importing: Bool

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Image(systemName: "camera.aperture").font(.system(size: 72)).foregroundStyle(.orange)
            Text("Flashback ONE35 V2").font(.title2.bold())
            Text("Unload a ONE35 roll, or import other RAW and JPEG images from Files.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button("Unload to Photo Lab") { importing = true }
                .buttonStyle(.borderedProminent).tint(.orange)
            Spacer()
        }
        .padding(28)
        .navigationTitle("My Camera")
    }
}

private struct RollListView: View {
    @EnvironmentObject private var store: RollStore
    let title: String
    let developedOnly: Bool

    private var visibleRolls: [FilmRoll] {
        developedOnly ? store.rolls.filter { $0.developedCount > 0 } : store.rolls
    }

    var body: some View {
        List(visibleRolls) { roll in
            NavigationLink(value: roll.id) {
                VStack(alignment: .leading) {
                    Text(roll.name).font(.headline)
                    Text(developedOnly
                         ? "\(roll.developedCount) developed · \(roll.importedAt.formatted(date: .abbreviated, time: .omitted))"
                         : "\(roll.frames.count) negatives · \(roll.importedAt.formatted(date: .abbreviated, time: .omitted))")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .overlay { if visibleRolls.isEmpty { ContentUnavailableView(developedOnly ? "No developed photos" : "No rolls", systemImage: "film") } }
        .navigationTitle(title)
        .navigationDestination(for: UUID.self) { id in
            RollDetailView(rollID: id, developedOnly: developedOnly)
        }
    }
}
