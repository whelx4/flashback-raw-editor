import Foundation
import CoreImage
import CoreImage.CIFilterBuiltins
import UIKit
import UniformTypeIdentifiers

/// Universal iPhone/iPad processor for finished compact-camera images.
/// It consumes the same preset catalog as the Windows editor.
final class ImageProcessor: @unchecked Sendable {
    static let shared = ImageProcessor()
    private let context = CIContext(options: [.cacheIntermediates: true])
    private var cubes: [String: (dimension: Int, data: Data)] = [:]
    private let renderLock = NSLock()

    func render(url: URL, preset: Preset, intensity: Double, maxDimension: CGFloat = 1800) throws -> UIImage {
        renderLock.lock()
        defer { renderLock.unlock() }
        guard let neutral = CIImage(
            contentsOf: url,
            options: [.applyOrientationProperty: true]
        ) else { throw RenderError.cannotDecode }
        let lutInput = neutral
        let cube = try loadCube(named: preset.iosLUT)
        let colorCube = CIFilter.colorCube()
        colorCube.inputImage = lutInput
        colorCube.cubeDimension = Float(cube.dimension)
        colorCube.cubeData = cube.data
        guard var styled = colorCube.outputImage else { throw RenderError.cannotRender }
        styled = applyCameraCharacter(to: styled, preset: preset, sourceIsJPEG: true)

        let amount = min(max(intensity, 0), 1)
        let mask = CIImage(color: CIColor(red: 1, green: 1, blue: 1, alpha: amount))
            .cropped(to: neutral.extent)
        let blend = CIFilter.blendWithAlphaMask()
        blend.inputImage = styled
        blend.backgroundImage = neutral
        blend.maskImage = mask
        guard let output = blend.outputImage else { throw RenderError.cannotRender }

        let scale = min(1, maxDimension / max(output.extent.width, output.extent.height))
        let preview = output.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cgImage = context.createCGImage(preview, from: preview.extent) else {
            throw RenderError.cannotRender
        }
        return UIImage(cgImage: cgImage)
    }

    private func applyCameraCharacter(to input: CIImage, preset: Preset,
                                      sourceIsJPEG: Bool) -> CIImage {
        let extent = input.extent
        let optics = preset.opticalAmounts
        let texture = preset.textureAmounts
        var image = input

        if optics.vignette > 0,
           let filter = CIFilter(name: "CIVignette", parameters: [
            kCIInputImageKey: image,
            kCIInputIntensityKey: optics.vignette,
            kCIInputRadiusKey: min(extent.width, extent.height) * 0.72
           ]), let output = filter.outputImage {
            image = output.cropped(to: extent)
        }

        if optics.edgeSoftness > 0,
           let blur = CIFilter(name: "CIGaussianBlur", parameters: [
            kCIInputImageKey: image, kCIInputRadiusKey: 2.5 * optics.edgeSoftness
           ])?.outputImage,
           let mask = radialEdgeMask(extent: extent),
           let blend = CIFilter(name: "CIBlendWithMask", parameters: [
            kCIInputImageKey: blur, kCIInputBackgroundImageKey: image,
            kCIInputMaskImageKey: mask
           ])?.outputImage {
            image = blend.cropped(to: extent)
        }

        if optics.softness > 0,
           let output = CIFilter(name: "CIGaussianBlur", parameters: [
            kCIInputImageKey: image, kCIInputRadiusKey: optics.softness
           ])?.outputImage {
            image = output.cropped(to: extent)
        }

        if optics.brownCorners, let mask = radialEdgeMask(extent: extent),
           let tinted = CIFilter(name: "CIColorMatrix", parameters: [
            kCIInputImageKey: image,
            "inputRVector": CIVector(x: 1.0, y: 0, z: 0, w: 0),
            "inputGVector": CIVector(x: 0, y: 0.84, z: 0, w: 0),
            "inputBVector": CIVector(x: 0, y: 0, z: 0.68, w: 0),
            "inputAVector": CIVector(x: 0, y: 0, z: 0, w: 1)
           ])?.outputImage,
           let blend = CIFilter(name: "CIBlendWithMask", parameters: [
            kCIInputImageKey: tinted, kCIInputBackgroundImageKey: image,
            kCIInputMaskImageKey: mask
           ])?.outputImage {
            image = blend.cropped(to: extent)
        }

        if texture.grain > 0 || texture.lumaNoise > 0 || texture.chromaNoise > 0,
           var noise = CIFilter(name: "CIRandomGenerator")?.outputImage {
            if texture.chromaNoise == 0,
               let mono = CIFilter(name: "CIColorControls", parameters: [
                kCIInputImageKey: noise, kCIInputSaturationKey: 0.0
               ])?.outputImage { noise = mono }
            let amount = max(texture.grain, texture.lumaNoise + texture.chromaNoise)
            if let alpha = CIFilter(name: "CIColorMatrix", parameters: [
                kCIInputImageKey: noise,
                "inputAVector": CIVector(x: 0, y: 0, z: 0, w: CGFloat(amount))
            ])?.outputImage,
               let blended = CIFilter(name: "CIOverlayBlendMode", parameters: [
                kCIInputImageKey: alpha.cropped(to: extent),
                kCIInputBackgroundImageKey: image
               ])?.outputImage {
                image = blended.cropped(to: extent)
            }
        }

        if optics.sharpen > 0,
           let output = CIFilter(name: "CISharpenLuminance", parameters: [
            kCIInputImageKey: image, kCIInputSharpnessKey: optics.sharpen
           ])?.outputImage { image = output.cropped(to: extent) }

        // Do not stack simulated compression on a finished compact-camera file.
        if preset.usesDigitalTexture && !sourceIsJPEG && texture.jpegQuality < 1,
           let cg = context.createCGImage(image, from: extent),
           let data = UIImage(cgImage: cg).jpegData(compressionQuality: CGFloat(texture.jpegQuality)),
           let decoded = CIImage(data: data) {
            image = decoded.transformed(by: CGAffineTransform(
                translationX: extent.origin.x - decoded.extent.origin.x,
                y: extent.origin.y - decoded.extent.origin.y))
        }
        return image.cropped(to: extent)
    }

    func jpegData(for image: UIImage, preservingMetadataFrom sourceURL: URL,
                  quality: Double = 0.95) throws -> Data {
        guard let cgImage = image.cgImage else { throw RenderError.cannotRender }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            output, UTType.jpeg.identifier as CFString, 1, nil
        ) else { throw RenderError.cannotRender }

        var properties: [CFString: Any] = [:]
        if let source = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
           let sourceProperties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)
                as? [CFString: Any] {
            properties = sourceProperties
        }
        properties[kCGImagePropertyOrientation] = 1
        properties[kCGImageDestinationLossyCompressionQuality] = quality
        properties[kCGImagePropertyPixelWidth] = cgImage.width
        properties[kCGImagePropertyPixelHeight] = cgImage.height
        CGImageDestinationAddImage(destination, cgImage, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else {
            throw RenderError.cannotRender
        }
        return output as Data
    }

    private func radialEdgeMask(extent: CGRect) -> CIImage? {
        let center = CIVector(x: extent.midX, y: extent.midY)
        let inner = min(extent.width, extent.height) * 0.30
        let outer = hypot(extent.width, extent.height) * 0.50
        return CIFilter(name: "CIRadialGradient", parameters: [
            "inputCenter": center, "inputRadius0": inner, "inputRadius1": outer,
            "inputColor0": CIColor.black, "inputColor1": CIColor.white
        ])?.outputImage?.cropped(to: extent)
    }

    private func loadCube(named filename: String) throws -> (dimension: Int, data: Data) {
        if let cached = cubes[filename] { return cached }
        let stem = URL(fileURLWithPath: filename).deletingPathExtension().lastPathComponent
        guard let url = Bundle.main.url(forResource: stem, withExtension: "cube"),
              let text = try? String(contentsOf: url, encoding: .utf8)
        else { throw RenderError.missingLUT(filename) }

        var dimension = 0
        var rgb: [SIMD3<Float>] = []
        for rawLine in text.split(whereSeparator: \Character.isNewline) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("LUT_3D_SIZE") {
                dimension = Int(line.split(separator: " ").last ?? "0") ?? 0
            } else if !line.isEmpty && !line.hasPrefix("#") {
                let values = line.split(whereSeparator: \Character.isWhitespace).compactMap { Float($0) }
                if values.count >= 3 { rgb.append(SIMD3(values[0], values[1], values[2])) }
            }
        }
        guard dimension > 1, rgb.count == dimension * dimension * dimension else {
            throw RenderError.invalidLUT(filename)
        }
        // .cube stores B fastest (source index r*N*N + g*N + b), while
        // CIColorCube expects R fastest (destination order b, g, r). Reorder
        // explicitly; passing file rows through unchanged swaps LUT axes.
        var rgba: [Float] = []
        rgba.reserveCapacity(rgb.count * 4)
        for b in 0..<dimension {
            for g in 0..<dimension {
                for r in 0..<dimension {
                    let value = rgb[r * dimension * dimension + g * dimension + b]
                    rgba.append(contentsOf: [value.x, value.y, value.z, 1])
                }
            }
        }
        let result = (dimension, rgba.withUnsafeBytes { Data($0) })
        cubes[filename] = result
        return result
    }

    enum RenderError: LocalizedError {
        case cannotDecode, cannotRender, missingLUT(String), invalidLUT(String)
        var errorDescription: String? {
            switch self {
            case .cannotDecode: return "This photo could not be decoded."
            case .cannotRender: return "The preview could not be rendered."
            case .missingLUT(let name): return "Missing preset LUT: \(name)"
            case .invalidLUT(let name): return "Invalid preset LUT: \(name)"
            }
        }
    }
}
