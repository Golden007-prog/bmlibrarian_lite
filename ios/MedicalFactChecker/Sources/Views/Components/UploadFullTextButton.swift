// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.

import SwiftUI
import SwiftData

/// "Upload Full Text" for a paper with no full text the analysis can read.
///
/// Placed at the foot of each paper card on both platforms; it shows itself
/// only while ``FullTextUpload/isOffered(for:)`` holds, or while it has
/// something to say, so a failed save is not hidden by the text it stored.
/// "Retry Full Text Fetching" then redoes the analysis of a relevant paper
/// with the text.
struct UploadFullTextButton: View {
    let document: Document

    /// Called with the stored result once an upload is saved.
    var onUploaded: ((AppFullTextResult) -> Void)?

    @Environment(\.modelContext) private var modelContext

    @State private var showImporter = false
    @State private var isProcessing = false
    @State private var message: String?

    var body: some View {
        if FullTextUpload.isOffered(for: document) || isProcessing || message != nil {
            HStack(spacing: 8) {
                if FullTextUpload.isOffered(for: document) || isProcessing {
                    Button {
                        showImporter = true
                    } label: {
                        if isProcessing {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Label("Upload Full Text", systemImage: "square.and.arrow.up")
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(isProcessing)
                    .accessibilityHint("Upload a PDF, HTML, Markdown, or plain-text file containing the full text")
                }

                if let message {
                    Text(message)
                        .font(.caption)
                        .foregroundColor(.red)
                        .lineLimit(4)
                }
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: FullTextUpload.allowedContentTypes) { result in
                switch result {
                case .success(let url):
                    upload(url)
                case .failure(let error):
                    message = error.localizedDescription
                }
            }
        }
    }

    /// Read the chosen file off the main actor, store it on the document, and
    /// say what went wrong, if anything.
    private func upload(_ url: URL) {
        isProcessing = true
        message = nil
        let pmid = document.pmid
        Task {
            do {
                let result = try await Task.detached {
                    try FullTextUpload.result(forFileAt: url, pmid: pmid)
                }.value
                document.applyFullTextResult(result)
                do {
                    try modelContext.save()
                } catch {
                    FullTextUpload.log.error(
                        "Could not save the full text uploaded for \(pmid, privacy: .public): \(error.localizedDescription, privacy: .public)"
                    )
                    message = "The full text could not be saved: \(error.localizedDescription)"
                    isProcessing = false
                    return
                }
                onUploaded?(result)
                if FullTextUpload.isOffered(for: document) {
                    // A scan, typically: kept for reading, but nothing for the analysis
                    message = "No text could be read from this file, so the analysis cannot use it, "
                        + "and automatic retrieval no longer looks for this paper."
                }
            } catch {
                message = error.localizedDescription
            }
            isProcessing = false
        }
    }
}
