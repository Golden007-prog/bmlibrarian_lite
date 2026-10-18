# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The retraction badge a document card carries: RETRACTED, CONCERN, NOT CHECKED."""

from bmlib.publications.retraction_status import RetractionStatus
from PySide6.QtWidgets import QLabel, QWidget

from bmlibrarian_lite.resources.styles import ThemeColors, get_stylesheet_generator

from ..retraction_store import PaperCheck
from ..retraction_text import badge_text, paper_status_line


def status_stylesheet(status: RetractionStatus) -> str:
    """The stylesheet a status is drawn in: error and warning colours, a quiet line unchecked."""
    generator = get_stylesheet_generator()
    if status is RetractionStatus.RETRACTED:
        return generator.header_stylesheet(
            "font_small", ThemeColors.ERROR_BG, ThemeColors.ERROR_TEXT, "padding_small", "padding_tiny"
        )
    if status is RetractionStatus.CONCERN:
        return generator.header_stylesheet(
            "font_small", ThemeColors.WARNING_BG, ThemeColors.WARNING_TEXT, "padding_small", "padding_tiny"
        )
    return generator.label_stylesheet("font_small", ThemeColors.TEXT_MUTED)


class RetractionBadge(QLabel):
    """A paper's retraction status as a badge; hidden for clear.

    Attributes:
        check: The check the badge shows.
    """

    def __init__(self, check: PaperCheck, parent: QWidget | None = None) -> None:
        """Build the badge.

        Args:
            check: The paper's check.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.check = check
        status = check.verdict.status
        text = badge_text(status)
        self.setText(text or "")
        self.setVisible(text is not None)
        self.setToolTip(paper_status_line(check))
        self.setStyleSheet(status_stylesheet(status))
