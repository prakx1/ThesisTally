import Foundation
import ImageIO
import Vision

// stdin is the exact archived PNG, rasterized independently of the PDF text layer.
struct WordBox: Encodable {
    let text: String
    let x, y, width, height: Double
}

let bytes = FileHandle.standardInput.readDataToEndOfFile()
guard let source = CGImageSourceCreateWithData(bytes as CFData, nil),
      let frame = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }
let request = VNRecognizeTextRequest()
request.revision = VNRecognizeTextRequestRevision3
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]
request.minimumTextHeight = 0.003
try VNImageRequestHandler(cgImage: frame).perform([request])
let boxes: [WordBox] = (request.results ?? []).compactMap { item in
    guard let text = item.topCandidates(1).first?.string else { return nil }
    let rect = item.boundingBox
    return WordBox(text: text, x: rect.minX, y: rect.midY, width: rect.width, height: rect.height)
}
FileHandle.standardOutput.write(try JSONEncoder().encode(boxes))
