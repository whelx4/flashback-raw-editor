import Foundation

struct PresetCatalogDocument: Decodable {
    struct Intensity: Decodable {
        let minimum: Double
        let maximum: Double
        let `default`: Double
        let blendSpace: String
    }

    let schemaVersion: Int
    let cameraModel: String
    let workingColorSpace: String
    let intensity: Intensity
    let presets: [Preset]
}

struct Preset: Identifiable, Decodable, Hashable {
    let id: String
    let name: String
    let subtitle: String
    let exportSuffix: String
    let lut: String
    let category: String
    let status: String
    let colorProfile: String
    let opticalProfile: String
    let textureProfile: String

    var isExperimental: Bool { status == "experimental" }
    var iosLUT: String { "ios_\(id).cube" }
    var usesDigitalTexture: Bool {
        ["camp_snap_2", "paper_shoot", "doncamera_2"].contains(textureProfile)
    }

    var opticalAmounts: (vignette: Double, softness: Double, edgeSoftness: Double,
                         sharpen: Double, bloom: Double, brownCorners: Bool) {
        switch opticalProfile {
        case "funsaver": return (0.10, 0.25, 0.30, 0.18, 0.03, false)
        case "quicksnap": return (0.08, 0.20, 0.25, 0.15, 0.02, false)
        case "rapid_retro": return (0.12, 0.35, 0.40, 0.10, 0.03, false)
        case "lomo_simple_use": return (0.10, 0.30, 0.35, 0.12, 0.03, false)
        case "h35": return (0.10, 0.25, 0.45, 0.10, 0.02, false)
        case "camp_snap_2": return (0.05, 0.05, 0.15, 0.20, 0.01, false)
        case "paper_shoot_20mp": return (0.12, 0.15, 0.30, 0.12, 0.01, true)
        case "doncamera_2": return (0.07, 0.15, 0.20, 0.15, 0.02, false)
        default: return (0.20, 0.20, 0.0, 0.50, 0.03, false)
        }
    }

    var textureAmounts: (grain: Double, lumaNoise: Double, chromaNoise: Double,
                         jpegQuality: Double) {
        switch textureProfile {
        case "film_800": return (0.040, 0, 0, 1)
        case "film_400_visible": return (0.034, 0, 0, 1)
        case "film_400_fine": return (0.028, 0, 0, 1)
        case "film_200_half": return (0.024, 0, 0, 1)
        case "camp_snap_2": return (0, 0.005, 0.002, 0.95)
        case "paper_shoot": return (0, 0.004, 0.002, 0.93)
        case "doncamera_2": return (0, 0.007, 0.0035, 0.88)
        default: return (0.08, 0, 0, 1)
        }
    }
}

@MainActor
final class PresetCatalog: ObservableObject {
    @Published private(set) var document: PresetCatalogDocument?
    @Published private(set) var errorMessage: String?

    init() {
        do {
            guard let url = Bundle.main.url(forResource: "presets", withExtension: "json") else {
                throw CocoaError(.fileNoSuchFile)
            }
            document = try JSONDecoder().decode(
                PresetCatalogDocument.self,
                from: Data(contentsOf: url)
            )
        } catch {
            errorMessage = "Preset catalog could not be loaded: \(error.localizedDescription)"
        }
    }

    var presets: [Preset] { document?.presets ?? [] }

    func preset(id: String) -> Preset? {
        let aliases = [
            "disposable": "funsaver_800",
            "point_shoot": "cs2_standard",
            "rangefinder": "quicksnap_400",
            "monochrome": "paper_bw"
        ]
        let normalized = aliases[id] ?? id
        return presets.first { $0.id == normalized } ?? presets.first
    }
}
