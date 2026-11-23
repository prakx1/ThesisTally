import CoreGraphics
import Foundation
import Vision

// Input is an archived exchange PDF. Output is a list of on-device OCR boxes per page.
struct Block: Codable {
    let x, y, height: Double
    let text: String
}

func recognize(_ page: CGPDFPage) throws -> [Block] {
    let rect = page.getBoxRect(.cropBox)
    let scale = min(3.0, 2200.0 / max(rect.width, rect.height))
    let w = Int(rect.width * scale), h = Int(rect.height * scale)
    guard w > 0, h > 0,
        let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { throw CocoaError(.fileReadCorruptFile) }
    let canvas = CGRect(x: 0, y: 0, width: w, height: h)
    context.setFillColor(gray: 1, alpha: 1)
    context.fill(canvas)
    context.concatenate(page.getDrawingTransform(.cropBox, rect: canvas, rotate: 0,
                                                 preserveAspectRatio: true))
    context.drawPDFPage(page)
    guard let frame = context.makeImage() else { throw CocoaError(.fileReadCorruptFile) }
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage: frame).perform([request])
    return (request.results ?? []).compactMap { item in
        guard let text = item.topCandidates(1).first?.string else { return nil }
        let box = item.boundingBox
        return Block(x: box.minX, y: box.midY, height: box.height, text: text)
    }
}

let source = FileHandle.standardInput.readDataToEndOfFile()
guard let provider = CGDataProvider(data: source as CFData),
      let doc = CGPDFDocument(provider) else { exit(2) }
var result: [String: [Block]] = [:]
for token in CommandLine.arguments.dropFirst() {
    guard let i = Int(token), i > 0 && i <= doc.numberOfPages,
          let page = doc.page(at: i) else { exit(3) }
    result[token] = try autoreleasepool { try recognize(page) }
}
FileHandle.standardOutput.write(try JSONEncoder().encode(result))
