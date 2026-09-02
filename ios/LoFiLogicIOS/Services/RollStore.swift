import Foundation
import ImageIO
import Photos

struct ImportedPhoto: Sendable {
    let filename: String
    let data: Data
}

@MainActor
final class RollStore: ObservableObject {
    @Published private(set) var rolls: [FilmRoll] = []
    @Published var importError: String?

    private let files = FileManager.default
    private lazy var rootURL: URL = {
        let documents = files.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return documents.appendingPathComponent("Rolls", isDirectory: true)
    }()

    init() { loadIndex() }

    func frameURL(rollID: UUID, frame: RollFrame) -> URL {
        let batch = rootURL.appendingPathComponent(rollID.uuidString, isDirectory: true)
        let modern = batch.appendingPathComponent("Originals", isDirectory: true)
            .appendingPathComponent(frame.filename)
        if files.fileExists(atPath: modern.path) { return modern }
        // Read old on-device libraries without exposing the former terminology.
        return batch.appendingPathComponent("Negatives", isDirectory: true)
            .appendingPathComponent(frame.filename)
    }

    func developedURL(rollID: UUID, frame: RollFrame) -> URL? {
        guard let filename = frame.developedFilename else { return nil }
        return rootURL.appendingPathComponent(rollID.uuidString, isDirectory: true)
            .appendingPathComponent("Developed", isDirectory: true)
            .appendingPathComponent(filename)
    }

    @discardableResult
    func saveDevelopedJPEG(_ data: Data, rollID: UUID, frameID: UUID,
                           suffix: String) throws -> URL {
        guard let rollIndex = rolls.firstIndex(where: { $0.id == rollID }),
              let frameIndex = rolls[rollIndex].frames.firstIndex(where: { $0.id == frameID })
        else { throw ExportError.frameMissing }
        let frame = rolls[rollIndex].frames[frameIndex]
        let base = URL(fileURLWithPath: frame.filename)
            .deletingPathExtension().lastPathComponent
        let safeSuffix = suffix.replacingOccurrences(
            of: "[^A-Za-z0-9_-]", with: "-", options: .regularExpression)
        let filename = "\(base)_\(safeSuffix).jpg"
        let folder = rootURL.appendingPathComponent(rollID.uuidString)
            .appendingPathComponent("Developed", isDirectory: true)
        try files.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent(filename)
        try data.write(to: destination, options: .atomic)
        rolls[rollIndex].frames[frameIndex].developedFilename = filename
        try saveIndex()
        return destination
    }

    func importMedia(_ urls: [URL]) async {
        let scoped = urls.map { ($0, $0.startAccessingSecurityScopedResource()) }
        defer {
            for (url, accessed) in scoped where accessed {
                url.stopAccessingSecurityScopedResource()
            }
        }
        do {
            let valid = try collectSupportedMedia(urls)
            guard !valid.isEmpty else {
                throw ImportError.noSupportedFiles
            }

            var roll = FilmRoll(name: "Import \(rolls.count + 1)", frames: [])
            let originals = rootURL.appendingPathComponent(roll.id.uuidString)
                .appendingPathComponent("Originals", isDirectory: true)
            try files.createDirectory(at: originals, withIntermediateDirectories: true)

            for source in valid {
                let destination = uniqueDestination(for: source.lastPathComponent, in: originals)
                try files.copyItem(at: source, to: destination)
                roll.frames.append(RollFrame(
                    filename: destination.lastPathComponent,
                    sourceCamera: cameraModel(at: source)
                ))
            }
            rolls.insert(roll, at: 0)
            try saveIndex()
        } catch {
            importError = error.localizedDescription
        }
    }

    func importPhotoPayloads(_ payloads: [ImportedPhoto]) async {
        guard !payloads.isEmpty else { return }
        do {
            var batch = FilmRoll(name: "Import \(rolls.count + 1)", frames: [])
            let originals = rootURL.appendingPathComponent(batch.id.uuidString)
                .appendingPathComponent("Originals", isDirectory: true)
            try files.createDirectory(at: originals, withIntermediateDirectories: true)
            for payload in payloads {
                let destination = uniqueDestination(for: payload.filename, in: originals)
                try payload.data.write(to: destination, options: .atomic)
                batch.frames.append(RollFrame(
                    filename: destination.lastPathComponent,
                    sourceCamera: cameraModel(at: destination)
                ))
            }
            rolls.insert(batch, at: 0)
            try saveIndex()
        } catch {
            importError = error.localizedDescription
        }
    }

    func saveToPhotoLibrary(_ url: URL) async throws {
        let status = await withCheckedContinuation { continuation in
            PHPhotoLibrary.requestAuthorization(for: .addOnly) {
                continuation.resume(returning: $0)
            }
        }
        guard status == .authorized || status == .limited else {
            throw ExportError.photoLibraryDenied
        }
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetChangeRequest.creationRequestForAssetFromImage(atFileURL: url)
        }
    }

    func update(roll: FilmRoll) {
        guard let index = rolls.firstIndex(where: { $0.id == roll.id }) else { return }
        rolls[index] = roll
        try? saveIndex()
    }

    private let supportedExtensions: Set<String> = [
        "jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp"
    ]

    private func collectSupportedMedia(_ urls: [URL]) throws -> [URL] {
        var output: [URL] = []
        for url in urls {
            var isDirectory: ObjCBool = false
            if files.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue {
                let keys: [URLResourceKey] = [.isRegularFileKey]
                let iterator = files.enumerator(at: url, includingPropertiesForKeys: keys)
                while let child = iterator?.nextObject() as? URL {
                    if supportedExtensions.contains(child.pathExtension.lowercased()) {
                        output.append(child)
                    }
                }
            } else if supportedExtensions.contains(url.pathExtension.lowercased()) {
                output.append(url)
            }
        }
        return output
    }

    private func cameraModel(at url: URL) -> String? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        else { return nil }
        let tiff = properties[kCGImagePropertyTIFFDictionary] as? [CFString: Any]
        let make = (tiff?[kCGImagePropertyTIFFMake] as? String)?.trimmingCharacters(in: .whitespaces)
        let model = (tiff?[kCGImagePropertyTIFFModel] as? String)?.trimmingCharacters(in: .whitespaces)
        if let make, let model, !make.isEmpty, !model.isEmpty { return "\(make) \(model)" }
        return model ?? make
    }

    private func uniqueDestination(for filename: String, in folder: URL) -> URL {
        let source = URL(fileURLWithPath: filename)
        var candidate = folder.appendingPathComponent(filename)
        var counter = 2
        while files.fileExists(atPath: candidate.path) {
            candidate = folder.appendingPathComponent(
                "\(source.deletingPathExtension().lastPathComponent)-\(counter).\(source.pathExtension)"
            )
            counter += 1
        }
        return candidate
    }

    private var indexURL: URL { rootURL.appendingPathComponent("rolls.json") }

    private func loadIndex() {
        guard let data = try? Data(contentsOf: indexURL),
              let decoded = try? JSONDecoder().decode([FilmRoll].self, from: data)
        else { return }
        rolls = decoded
    }

    private func saveIndex() throws {
        try files.createDirectory(at: rootURL, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(rolls).write(to: indexURL, options: .atomic)
    }

    enum ImportError: LocalizedError {
        case noSupportedFiles
        var errorDescription: String? {
            "No supported JPEG or finished image files were found."
        }
    }

    enum ExportError: LocalizedError {
        case frameMissing, photoLibraryDenied
        var errorDescription: String? {
            switch self {
            case .frameMissing: return "The selected photo is no longer in this import."
            case .photoLibraryDenied: return "Allow LoFi Logic to add photos in Settings."
            }
        }
    }
}
