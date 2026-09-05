import SwiftUI
import UniformTypeIdentifiers
import PhotosUI

struct ContentView: View {
    @EnvironmentObject private var store: RollStore
    @State private var importing = false
    @State private var photoSelection: [PhotosPickerItem] = []

    private var importTypes: [UTType] {
        let extensions = ["jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp"]
        return [.folder] + extensions.compactMap { UTType(filenameExtension: $0) }
    }

    var body: some View {
        TabView {
            NavigationStack {
                CameraImportView(importing: $importing, photoSelection: $photoSelection)
            }
                .tabItem { Label("Import", systemImage: "camera") }
            NavigationStack { RollListView(title: "Photo Lab", developedOnly: false) }
                .tabItem { Label("Photo Lab", systemImage: "flask") }
            NavigationStack { RollListView(title: "Exports", developedOnly: true) }
                .tabItem { Label("Exports", systemImage: "photo.on.rectangle") }
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
        .onChange(of: photoSelection) { _, selection in
            guard !selection.isEmpty else { return }
            Task {
                var payloads: [ImportedPhoto] = []
                for (index, item) in selection.enumerated() {
                    guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
                    let type = item.supportedContentTypes.first { $0.conforms(to: .image) }
                    let ext = type?.preferredFilenameExtension ?? "jpg"
                    payloads.append(ImportedPhoto(
                        filename: String(format: "PHOTO_%03d.%@", index + 1, ext),
                        data: data
                    ))
                }
                await store.importPhotoPayloads(payloads)
                photoSelection = []
            }
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
    @Binding var photoSelection: [PhotosPickerItem]

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Image(systemName: "camera.aperture").font(.system(size: 72)).foregroundStyle(.orange)
            Text("Sony P43 Photo Lab").font(.title2.bold())
            Text("Bring in finished compact-camera photos from Apple Photos, Files, or a connected card reader.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
                .frame(maxWidth: 480)
            PhotosPicker(selection: $photoSelection, maxSelectionCount: 0, matching: .images) {
                Label("Choose from Photos", systemImage: "photo.on.rectangle.angled")
                    .frame(minWidth: 210)
            }
            .buttonStyle(.borderedProminent).tint(.orange)
            Button { importing = true } label: {
                Label("Choose Files or Folder", systemImage: "folder")
                    .frame(minWidth: 210)
            }
            .buttonStyle(.bordered)
            Text("P43 JPEGs start at 60% filter intensity so the camera's own colour and flash character remain visible.")
                .font(.footnote).foregroundStyle(.tertiary).multilineTextAlignment(.center)
                .frame(maxWidth: 480)
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
                         ? "\(roll.developedCount) exported · \(roll.importedAt.formatted(date: .abbreviated, time: .omitted))"
                         : "\(roll.frames.count) photos · \(roll.importedAt.formatted(date: .abbreviated, time: .omitted))")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .overlay { if visibleRolls.isEmpty { ContentUnavailableView(developedOnly ? "No exported photos" : "No imports", systemImage: "photo.stack") } }
        .navigationTitle(title)
        .navigationDestination(for: UUID.self) { id in
            RollDetailView(rollID: id, developedOnly: developedOnly)
        }
    }
}
