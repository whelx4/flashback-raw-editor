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
        case "funsaver": return (0.28, 0.35, 0.45, 0.65, 0.05, false)
        case "quicksnap": return (0.20, 0.30, 0.35, 0.70, 0.04, false)
        case "rapid_retro": return (0.36, 0.50, 0.60, 0.50, 0.05, false)
        case "lomo_simple_use": return (0.30, 0.40, 0.50, 0.60, 0.05, false)
        case "h35": return (0.24, 0.30, 0.60, 0.50, 0.04, false)
        case "camp_snap_2": return (0.16, 0.10, 0.30, 1.40, 0.02, false)
        case "paper_shoot_20mp": return (0.36, 0.25, 0.45, 0.60, 0.02, true)
        case "doncamera_2": return (0.20, 0.25, 0.35, 0.85, 0.04, false)
        default: return (0.20, 0.20, 0.0, 0.50, 0.03, false)
        }
    }

    var textureAmounts: (grain: Double, lumaNoise: Double, chromaNoise: Double,
                         jpegQuality: Double) {
        switch textureProfile {
        case "film_800": return (0.12, 0, 0, 1)
        case "film_400_visible": return (0.10, 0, 0, 1)
        case "film_400_fine": return (0.085, 0, 0, 1)
        case "film_200_half": return (0.09, 0, 0, 1)
        case "camp_snap_2": return (0, 0.014, 0.0045, 0.91)
        case "paper_shoot": return (0, 0.011, 0.005, 0.87)
        case "doncamera_2": return (0, 0.020, 0.012, 0.72)
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
