import Foundation
import ScreenCaptureKit
import Vision
import AppKit

struct ObserverError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

func output(_ value: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}
func recognize(_ image: CGImage) throws -> [[String: Any]] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "ja-JP", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image).perform([request])
    return (request.results ?? []).compactMap { row in
        guard let text = row.topCandidates(1).first else { return nil }
        let box = row.boundingBox
        return ["text": text.string, "confidence": text.confidence,
                "box": [box.minX, 1 - box.maxY, box.width, box.height]]
    }
}
@main struct Observer {
    @MainActor
    static func main() async {
        do {
            let args = CommandLine.arguments
            guard args.count >= 2 else { throw ObserverError(message: "需要 list / capture / ocr-file") }
            if args[1] == "ocr-file" {
                guard args.count == 3, let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[2]) as CFURL, nil),
                      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw ObserverError(message: "图片无法读取") }
                try output(["lines": recognize(image)])
                return
            }
            // A CLI must initialize its WindowServer connection before creating an SCContentFilter.
            let app = NSApplication.shared
            app.setActivationPolicy(.prohibited)
            guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
                throw ObserverError(message: "未获得屏幕录制权限：请在系统设置 → 隐私与安全性 → 屏幕与系统音频录制中允许启动终端或应用，然后重新启动桌宠。")
            }
            // Listing windows requests the macOS Screen Recording permission, never a whole-screen capture.
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            if args[1] == "list" {
                try output(content.windows.filter { $0.frame.width > 80 && $0.frame.height > 80 }.map {
                    ["id": $0.windowID, "title": $0.title ?? "", "app": $0.owningApplication?.applicationName ?? "", "width": $0.frame.width, "height": $0.frame.height] as [String: Any]
                })
                return
            }
            guard args[1] == "capture", args.count >= 3, let id = UInt32(args[2]),
                  let window = content.windows.first(where: { $0.windowID == id }) else { throw ObserverError(message: "所选窗口已关闭或不可用，请重新选择并预览") }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let configuration = SCStreamConfiguration()
            configuration.width = Int(filter.contentRect.width * CGFloat(filter.pointPixelScale))
            configuration.height = Int(filter.contentRect.height * CGFloat(filter.pointPixelScale))
            configuration.showsCursor = false
            configuration.ignoreShadowsSingleWindow = true
            var image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
            if args.count == 7 {
                let numbers = args[3...6].compactMap(Double.init)
                guard numbers.count == 4, numbers.allSatisfy({ $0 >= 0 && $0 <= 1 }), numbers[2] > 0, numbers[3] > 0,
                      numbers[0] + numbers[2] <= 1.001, numbers[1] + numbers[3] <= 1.001 else { throw ObserverError(message: "框选区域无效") }
                let rect = CGRect(x: numbers[0] * Double(image.width), y: numbers[1] * Double(image.height), width: numbers[2] * Double(image.width), height: numbers[3] * Double(image.height))
                guard let cropped = image.cropping(to: rect) else { throw ObserverError(message: "裁剪失败") }
                image = cropped
            }
            let bitmap = NSBitmapImageRep(cgImage: image)
            let data = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.75])!
            try output(["lines": recognize(image), "image": "data:image/jpeg;base64," + data.base64EncodedString(), "width": image.width, "height": image.height])
        } catch {
            FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
