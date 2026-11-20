// natter-sysaudio: what the Mac is playing, as raw PCM on stdout.
//
// Natter's Python recorder starts this helper and reads its stdout:
// 16-bit signed little-endian mono samples at 48 kHz (the rate is printed on
// the "ready" line so Python never has to guess). Status lines go to stderr:
//   ready 48000         the stream is running
//   error <plain words> it could not start (then exit code 2)
// macOS asks for permission on behalf of the app that started the helper
// (Natter.app when packaged; Terminal or the IDE when run from source), so
// sign the helper with the app and ship it inside the bundle.
//
// Needs macOS 13+ and the "Screen & System Audio Recording" permission.
// Stops when stdin closes or on SIGTERM.

import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

let sampleRate = 48000
setvbuf(stdout, nil, _IONBF, 0)

func status(_ line: String) {
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}

final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let audioQueue = DispatchQueue(label: "natter.sysaudio.audio")
    private let videoQueue = DispatchQueue(label: "natter.sysaudio.video")
    private let out = FileHandle.standardOutput

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "natter", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "no display found"])
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = sampleRate
        config.channelCount = 1
        // Video cannot be switched off; keep it tiny and rare.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 10, timescale: 1)
        config.queueDepth = 3

        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: videoQueue)
        try await s.startCapture()
        stream = s
    }

    func stop() {
        let sem = DispatchSemaphore(value: 0)
        if let s = stream {
            s.stopCapture { _ in sem.signal() }
            _ = sem.wait(timeout: .now() + 2)
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, CMSampleBufferGetNumSamples(sb) > 0 else { return }
        guard let fmt = CMSampleBufferGetFormatDescription(sb),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fmt) else { return }
        let asbd = asbdPtr.pointee
        let isFloat = asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0
        let nonInterleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        let channels = Int(asbd.mChannelsPerFrame)
        guard isFloat, asbd.mBitsPerChannel == 32, channels >= 1 else { return }

        var blockBuffer: CMBlockBuffer?
        var abl = AudioBufferList()
        let st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil, bufferListOut: &abl,
            bufferListSize: MemoryLayout<AudioBufferList>.size, blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer)
        guard st == noErr else { return }
        let buffers = UnsafeMutableAudioBufferListPointer(&abl)
        guard let first = buffers.first, let raw = first.mData else { return }

        // Mono from the first buffer (a non-interleaved stereo stream would have
        // one buffer per channel; averaging the first is close enough for speech
        // and we asked for mono anyway).
        let floats = raw.assumingMemoryBound(to: Float.self)
        let frames = nonInterleaved ? Int(first.mDataByteSize) / 4
                                    : Int(first.mDataByteSize) / 4 / channels
        let step = nonInterleaved ? 1 : channels
        var pcm = [Int16](repeating: 0, count: frames)
        for i in 0..<frames {
            var acc: Float = 0
            for c in 0..<step { acc += floats[i * step + c] }
            let v = max(-1, min(1, acc / Float(step)))
            pcm[i] = Int16(v * 32767)
        }
        let data = pcm.withUnsafeBufferPointer { Data(buffer: $0) }
        do { try out.write(contentsOf: data) } catch { exit(0) }   // reader went away
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        status("error stream stopped: \(error.localizedDescription)")
        exit(3)
    }
}

let capture = Capture()

signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { capture.stop(); exit(0) }
term.resume()

// The parent closing our stdin means it is done with us.
DispatchQueue.global().async {
    while FileHandle.standardInput.availableData.count > 0 {}
    capture.stop()
    exit(0)
}

Task {
    do {
        try await capture.start()
        status("ready \(sampleRate)")
    } catch {
        let text = error.localizedDescription
        let ns = error as NSError
        if ns.domain == SCStreamErrorDomain && ns.code == SCStreamError.userDeclined.rawValue {
            status("error permission: allow Natter in System Settings → Privacy & Security → Screen & System Audio Recording")
        } else {
            status("error \(text)")
        }
        exit(2)
    }
}

dispatchMain()
