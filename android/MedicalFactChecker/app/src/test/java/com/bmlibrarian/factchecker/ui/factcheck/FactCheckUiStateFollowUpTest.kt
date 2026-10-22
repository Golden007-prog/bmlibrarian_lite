/*
 * BMLibrarian Lite - Biomedical Literature Research Tool
 * Copyright (C) 2024-2026 Dr Horst Herb
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 */

package com.bmlibrarian.factchecker.ui.factcheck

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.domain.workflow.WorkflowState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** After a search, the submit button finds more evidence for the same claim; an edited claim is a new question. */
class FactCheckUiStateFollowUpTest {

    private val searched = FactCheckUiState(
        claimText = "Aspirin prevents strokes",
        searchedClaim = "Aspirin prevents strokes",
        documents = listOf(DocumentEntity(sessionId = "s", title = "Aspirin and stroke"))
    )

    @Test
    fun `the claim a session searched finds more evidence for it`() {
        assertTrue(searched.isFollowUp)
        assertEquals("Find More Evidence", searched.submitLabel)
        assertTrue(searched.canSubmit)
    }

    @Test
    fun `an edited claim is a new question`() {
        val edited = searched.copy(claimText = "Aspirin prevents heart attacks")

        assertFalse(edited.isFollowUp)
        assertEquals("Check Claim", edited.submitLabel)
    }

    @Test
    fun `a search that found nothing yet is not followed up`() {
        assertFalse(searched.copy(documents = emptyList()).isFollowUp)
    }

    @Test
    fun `a follow-up paused for the user's decision is answered by that decision`() {
        val paused = searched.copy(workflowState = WorkflowState.AwaitingUserDecision(2, 10, 30))

        assertFalse(paused.canSubmit)
        assertTrue("a new question may still be asked", paused.copy(claimText = "Another claim").canSubmit)
    }
}
