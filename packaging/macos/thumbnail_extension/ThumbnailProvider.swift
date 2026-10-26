// Quick Look thumbnail extension: shows a .pman project's saved thumbnail as
// its Finder icon.
//
// The app stores the thumbnail as the first, uncompressed entry of the zip
// (see FORMAT.md, "thumbnail.png"), so reading it needs only the local file
// header at offset 0 -- no zip library.
import CoreGraphics
import Foundation
import ImageIO
import QuickLookThumbnailing

private let thumbnailName = "thumbnail.png"
// The app shrinks it to 512 px; anything far bigger is not ours.
private let maxThumbnailBytes = 16 * 1024 * 1024

enum ThumbnailError: Error {
    case missing
}

private func readThumbnailPNG(at url: URL) throws -> Data {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    let header = try handle.read(upToCount: 30) ?? Data()
    guard header.count == 30 else { throw ThumbnailError.missing }
    func u16(_ offset: Int) -> Int {
        Int(header[offset]) | Int(header[offset + 1]) << 8
    }
    func u32(_ offset: Int) -> Int {
        u16(offset) | u16(offset + 2) << 16
    }
    let signature = u32(0)
    let flags = u16(6)
    let method = u16(8)
    let size = u32(18)
    let nameLength = u16(26)
    let extraLength = u16(28)
    // Stored (method 0), sizes in the header (no data descriptor, flag bit 3).
    guard signature == 0x0403_4B50, method == 0, flags & 0x8 == 0,
          size > 0, size <= maxThumbnailBytes
    else { throw ThumbnailError.missing }
    let name = try handle.read(upToCount: nameLength) ?? Data()
    guard String(data: name, encoding: .utf8) == thumbnailName else {
        throw ThumbnailError.missing
    }
    try handle.seek(toOffset: UInt64(30 + nameLength + extraLength))
    let png = try handle.read(upToCount: size) ?? Data()
    guard png.count == size else { throw ThumbnailError.missing }
    return png
}

class ThumbnailProvider: QLThumbnailProvider {
    override func provideThumbnail(
        for request: QLFileThumbnailRequest,
        _ handler: @escaping (QLThumbnailReply?, Error?) -> Void
    ) {
        do {
            let png = try readThumbnailPNG(at: request.fileURL)
            guard let source = CGImageSourceCreateWithData(png as CFData, nil),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
            else { throw ThumbnailError.missing }
            let maximum = request.maximumSize
            let scale = min(
                maximum.width / CGFloat(image.width),
                maximum.height / CGFloat(image.height)
            )
            let size = CGSize(
                width: max(1, (CGFloat(image.width) * scale).rounded()),
                height: max(1, (CGFloat(image.height) * scale).rounded())
            )
            handler(QLThumbnailReply(contextSize: size) { context in
                context.interpolationQuality = .high
                context.draw(image, in: CGRect(origin: .zero, size: size))
                return true
            }, nil)
        } catch {
            // No thumbnail (older file, iPad build): Finder keeps the plain icon.
            handler(nil, error)
        }
    }
}
