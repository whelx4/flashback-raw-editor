import Foundation

struct RollFrame: Identifiable, Codable, Hashable {
    let id: UUID
    let filename: String
    var presetID: String
    var intensity: Double
    var sourceCamera: String?
    var developedFilename: String?

    init(filename: String, presetID: String = "funsaver_800", intensity: Double = 0.6,
         sourceCamera: String? = nil, developedFilename: String? = nil) {
        self.id = UUID()
        self.filename = filename
        self.presetID = presetID
        self.intensity = intensity
        self.sourceCamera = sourceCamera
        self.developedFilename = developedFilename
    }

    var isDeveloped: Bool { developedFilename != nil }
}

struct FilmRoll: Identifiable, Codable, Hashable {
    let id: UUID
    var name: String
    let importedAt: Date
    var frames: [RollFrame]

    init(name: String, frames: [RollFrame]) {
        self.id = UUID()
        self.name = name
        self.importedAt = Date()
        self.frames = frames
    }

    var developedCount: Int { frames.filter(\.isDeveloped).count }
}
