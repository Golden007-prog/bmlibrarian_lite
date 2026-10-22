/*
 * BMLibrarian Lite - Biomedical Literature Research Tool
 * Copyright (C) 2024-2025 Dr Horst Herb
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

package com.bmlibrarian.factchecker.ui.factcheck.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.bmlibrarian.factchecker.util.Constants

/**
 * Input component for the medical claim.
 *
 * Provides a multi-line text input for entering medical claims, a submit
 * button that checks a claim or, after a search, finds more evidence for it,
 * and a button that clears the claim for a new question.
 *
 * @param claimText Current text in the input field
 * @param onClaimTextChange Callback when text changes
 * @param onSubmit Callback when submit button is pressed
 * @param isEnabled Whether the input is enabled for interaction
 * @param submitLabel The submit button's text
 * @param canSubmit Whether the submit button may be pressed, beyond [isEnabled] and a non-blank claim
 * @param onNewQuestion Callback when the user clears the claim for a new one; null hides the button
 * @param modifier Modifier for the component
 */
@Composable
fun ClaimInput(
    claimText: String,
    onClaimTextChange: (String) -> Unit,
    onSubmit: () -> Unit,
    isEnabled: Boolean,
    submitLabel: String = "Check Claim",
    canSubmit: Boolean = true,
    onNewQuestion: (() -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier) {
        OutlinedTextField(
            value = claimText,
            onValueChange = onClaimTextChange,
            label = { Text("Medical Claim") },
            placeholder = { Text("Enter a medical claim to fact-check...") },
            enabled = isEnabled,
            minLines = 3,
            maxLines = 5,
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(Constants.UI_ICON_TEXT_SPACING.dp))

        Button(
            onClick = onSubmit,
            enabled = isEnabled && canSubmit && claimText.isNotBlank(),
            modifier = Modifier.align(Alignment.End)
        ) {
            Icon(
                imageVector = Icons.Default.Search,
                contentDescription = null,
                modifier = Modifier.size(Constants.UI_ICON_SIZE.dp)
            )
            Spacer(modifier = Modifier.width(Constants.UI_ELEMENT_SPACING.dp))
            Text(submitLabel)
        }

        if (onNewQuestion != null) {
            Spacer(modifier = Modifier.height(Constants.UI_ICON_TEXT_SPACING.dp))
            OutlinedButton(
                onClick = onNewQuestion,
                enabled = isEnabled,
                modifier = Modifier.align(Alignment.End)
            ) {
                Text("New Question")
            }
        }
    }
}
