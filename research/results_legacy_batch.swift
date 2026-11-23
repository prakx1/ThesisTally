import Foundation
import ImageIO
import Vision

// One independent Vision request per original page. Processing several PNGs in the
// same process reuses the framework's startup, without mixing any OCR observations.
struct PageWord: Encodable {
    let text: String
    let x, y, width, height: Double
}

for path in CommandLine.arguments.dropFirst() {
    try autoreleasepool {
        let bytes = try Data(contentsOf: URL(fileURLWithPath: path))
        guard let source = CGImageSourceCreateWithData(bytes as CFData, nil),
              let frame = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }
        let request = VNRecognizeTextRequest()
        request.revision = VNRecognizeTextRequestRevision3
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US"]
        request.minimumTextHeight = 0.003
        try VNImageRequestHandler(cgImage: frame).perform([request])
        let boxes: [PageWord] = (request.results ?? []).compactMap { item in
            guard let text = item.topCandidates(1).first?.string else { return nil }
            let rect = item.boundingBox
            return PageWord(text: text, x: rect.minX, y: rect.midY, width: rect.width, height: rect.height)
        }
        FileHandle.standardOutput.write(try JSONEncoder().encode(boxes))
        FileHandle.standardOutput.write(Data([10]))
    }
}
