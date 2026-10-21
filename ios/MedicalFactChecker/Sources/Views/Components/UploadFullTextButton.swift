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
/// Shown below each paper card on both platforms (``FullTextUpload/isOffered(for:)``).
/// "Retry Full Text Fetching" then redoes the paper's analysis with the text.
struct UploadFullTextButton: View {
    let document: Document

    @Environment(\.modelContext) private var modelContext

    @State private var showImporter = false
    @State private var isProcessing = false
    @State private var message: String?

    var body: some View {
        HStack(spacing: 8) {
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
            .accessibilityHint("Upload a PDF, HTML, or Markdown file containing the full text")

            if let message {
                Text(message)
                    .font(.caption)
                    .foregroundColor(.red)
                    .lineLimit(3)
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
                try modelContext.save()
                if FullTextUpload.isOffered(for: document) {
                    // A scan, typically: kept for reading, but nothing for the analysis
                    message = "No text could be read from this file, so the analysis cannot use it."
                }
            } catch {
                message = error.localizedDescription
            }
            isProcessing = false
        }
    }
}
