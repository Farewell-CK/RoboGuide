package com.robonix.client.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.robonix.client.data.model.ClientSettings
import com.robonix.client.ui.i18n.AppStrings
import com.robonix.client.ui.i18n.t
import com.robonix.client.ui.i18n.tStatus
import com.robonix.client.ui.navigation.SharedViewModel
import com.robonix.client.ui.theme.*

private val languageOptions = listOf(
    AppStrings.SYSTEM to "settings.language.system",
    AppStrings.ZH to "settings.language.zh",
    AppStrings.EN to "settings.language.en",
)

@Composable
fun SettingsScreen(
    sharedViewModel: SharedViewModel = hiltViewModel(),
) {
    val settings by sharedViewModel.settings.collectAsState()
    val connectionState by sharedViewModel.connectionState.collectAsState()
    val snapshot by sharedViewModel.systemSnapshot.collectAsState()

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // Connection Settings
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(t("settings.connection"), color = Text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    Text(t("settings.connection.desc"), color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(12.dp))

                    OutlinedTextField(
                        value = settings.robotHost,
                        onValueChange = { v: String -> sharedViewModel.updateSettings { s: ClientSettings -> s.copy(robotHost = v) } },
                        label = { Text(t("settings.host")) },
                        placeholder = { Text("100.x.y.z") },
                        leadingIcon = { Icon(Icons.Default.Computer, null, modifier = Modifier.size(20.dp)) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = inputColors(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(8.dp))

                    OutlinedTextField(
                        value = settings.atlasPort.toString(),
                        onValueChange = {
                            val port = it.toIntOrNull() ?: 50051
                            sharedViewModel.updateSettings { s -> s.copy(atlasPort = if (it.isBlank()) 50051 else port) }
                        },
                        label = { Text(t("settings.port")) },
                        placeholder = { Text("50051") },
                        leadingIcon = { Icon(Icons.Default.Tag, null, modifier = Modifier.size(20.dp)) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = inputColors(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    )
                    Spacer(Modifier.height(8.dp))

                    OutlinedTextField(
                        value = settings.liaisonEndpoint,
                        onValueChange = { v: String -> sharedViewModel.updateSettings { s: ClientSettings -> s.copy(liaisonEndpoint = v) } },
                        label = { Text(t("settings.liaison")) },
                        placeholder = { Text(t("settings.liaison.placeholder")) },
                        leadingIcon = { Icon(Icons.Default.Router, null, modifier = Modifier.size(20.dp)) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = inputColors(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(8.dp))

                    OutlinedTextField(
                        value = settings.userId,
                        onValueChange = { v -> sharedViewModel.updateSettings { s -> s.copy(userId = v) } },
                        label = { Text(t("settings.userid")) },
                        placeholder = { Text("voice:client") },
                        leadingIcon = { Icon(Icons.Default.Person, null, modifier = Modifier.size(20.dp)) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = inputColors(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(12.dp))

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = { sharedViewModel.saveSettings() },
                            colors = ButtonDefaults.buttonColors(containerColor = Blue),
                            shape = RoundedCornerShape(8.dp),
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Default.Save, null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text(t("action.save"), fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                        Button(
                            onClick = { sharedViewModel.connect() },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (connectionState.isOnline) Green.copy(alpha = 0.2f) else Cyan
                            ),
                            shape = RoundedCornerShape(8.dp),
                            modifier = Modifier.weight(1f),
                            enabled = settings.atlasEndpoint.isNotBlank(),
                        ) {
                            Icon(
                                if (connectionState.isOnline) Icons.Default.Check else Icons.Default.PlayArrow,
                                null,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(4.dp))
                            Text(
                                if (connectionState.isOnline) t("action.connected") else t("action.connect"),
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                }
            }
        }

        // Preferences (language + voice input)
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(t("settings.prefs"), color = Text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    Text(t("settings.prefs.desc"), color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(6.dp))

                    // Language — row opens a picker dialog
                    var showLangDialog by remember { mutableStateOf(false) }
                    val currentLangLabel = languageOptions
                        .firstOrNull { it.first == settings.language }?.second
                        ?: "settings.language.system"
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .clickable { showLangDialog = true }
                            .padding(vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Default.Language, null, tint = Muted, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text(t("settings.language"), color = Text, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                        Spacer(Modifier.weight(1f))
                        Text(t(currentLangLabel), color = Muted, fontSize = 12.sp)
                        Icon(Icons.Default.ChevronRight, null, tint = Dim, modifier = Modifier.size(18.dp))
                    }

                    if (showLangDialog) {
                        AlertDialog(
                            onDismissRequest = { showLangDialog = false },
                            title = { Text(t("settings.language"), color = Text) },
                            containerColor = Panel,
                            text = {
                                Column {
                                    languageOptions.forEach { (value, labelKey) ->
                                        Row(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .clip(RoundedCornerShape(8.dp))
                                                .clickable {
                                                    sharedViewModel.updateSettings { it.copy(language = value) }
                                                    sharedViewModel.saveSettings()
                                                    showLangDialog = false
                                                }
                                                .padding(vertical = 6.dp),
                                            verticalAlignment = Alignment.CenterVertically,
                                        ) {
                                            RadioButton(
                                                selected = settings.language == value,
                                                onClick = {
                                                    sharedViewModel.updateSettings { it.copy(language = value) }
                                                    sharedViewModel.saveSettings()
                                                    showLangDialog = false
                                                },
                                            )
                                            Text(t(labelKey), color = Text, fontSize = 14.sp)
                                        }
                                    }
                                }
                            },
                            confirmButton = {},
                            dismissButton = {
                                TextButton(onClick = { showLangDialog = false }) {
                                    Text(t("action.cancel"), color = Muted)
                                }
                            },
                        )
                    }

                    // Voice clip length slider (hold-to-talk auto-stop)
                    Spacer(Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Mic, null, tint = Muted, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text(t("settings.record"), color = Text, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                        Spacer(Modifier.weight(1f))
                        Text(
                            t("settings.record.value", settings.recordSeconds),
                            color = Cyan, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                        )
                    }
                    Text(t("settings.record.desc"), color = Muted, fontSize = 11.sp)
                    Slider(
                        value = settings.recordSeconds.toFloat(),
                        onValueChange = { v ->
                            sharedViewModel.updateSettings { it.copy(recordSeconds = v.toInt().coerceIn(2, 60)) }
                        },
                        onValueChangeFinished = { sharedViewModel.saveSettings() },
                        valueRange = 2f..60f,
                        steps = 57,
                        colors = SliderDefaults.colors(
                            thumbColor = Cyan,
                            activeTrackColor = Cyan,
                            inactiveTrackColor = Line,
                        ),
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(t("settings.record.value", 2), color = Dim, fontSize = 10.sp)
                        Text(t("settings.record.value", 60), color = Dim, fontSize = 10.sp)
                    }
                }
            }
        }

        // System Snapshot
        snapshot?.let { snap ->
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Panel),
                    shape = RoundedCornerShape(10.dp),
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(t("settings.system"), color = Text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            RefreshButton { sharedViewModel.refreshSystem() }
                            StateBadge(snap.summary.state)
                        }
                        Spacer(Modifier.height(8.dp))

                        // Metrics
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            MetricCard(t("settings.metric.providers"), snap.summary.providers.toString(), Muted, Modifier.weight(1f))
                            MetricCard(t("settings.metric.active"), snap.summary.active.toString(), Green, Modifier.weight(1f))
                            MetricCard(t("settings.metric.errors"), snap.summary.errors.toString(), Red, Modifier.weight(1f))
                        }

                        Spacer(Modifier.height(12.dp))
                        Text(t("settings.contracts"), color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(4.dp))

                        snap.requiredContracts.forEach { contract ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 3.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(contract.label, color = Text, fontSize = 12.sp)
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(
                                        if (contract.available) Icons.Default.CheckCircle else Icons.Default.Cancel,
                                        null,
                                        tint = if (contract.available) Green else Red,
                                        modifier = Modifier.size(14.dp),
                                    )
                                    Spacer(Modifier.width(4.dp))
                                    Text(
                                        if (contract.available) contract.providers.firstOrNull() ?: t("settings.contract.ok")
                                        else t("settings.contract.missing"),
                                        color = if (contract.available) Green else Red,
                                        fontSize = 11.sp,
                                    )
                                }
                            }
                        }

                        if (snap.providers.isNotEmpty()) {
                            Spacer(Modifier.height(12.dp))
                            Text(
                                t("settings.providers", snap.providers.size),
                                color = Muted,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                            )
                            snap.providers.take(10).forEach { provider ->
                                Row(
                                    modifier = Modifier.padding(vertical = 2.dp),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    Text(provider.id, color = Text, fontSize = 11.sp)
                                    Text(provider.kind, color = Dim, fontSize = 10.sp)
                                    Text(tStatus(provider.state), color = stateColor(provider.state), fontSize = 10.sp, fontWeight = FontWeight.Bold)
                                }
                            }
                            if (snap.providers.size > 10) {
                                Text(t("settings.providers.more", snap.providers.size - 10), color = Muted, fontSize = 10.sp)
                            }
                        }

                        snap.error?.let { err ->
                            Spacer(Modifier.height(6.dp))
                            Surface(color = Red.copy(alpha = 0.1f), shape = RoundedCornerShape(6.dp)) {
                                Text(err, color = Red, fontSize = 11.sp, modifier = Modifier.padding(8.dp))
                            }
                        }
                    }
                }
            }
        } ?: item {
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(
                    modifier = Modifier.padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(Icons.Default.CloudOff, null, tint = Dim, modifier = Modifier.size(32.dp))
                    Spacer(Modifier.height(8.dp))
                    Text(t("settings.system.hint"), color = Muted, fontSize = 12.sp)
                }
            }
        }

        // About
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(t("app.name"), color = Text, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Text("Version 0.2.0-android", color = Muted, fontSize = 12.sp)
                    Text(t("settings.about.desc"), color = Dim, fontSize = 11.sp)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Tech: Kotlin · Jetpack Compose · gRPC · Material3",
                        color = Dim,
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }

        item { Spacer(Modifier.height(32.dp)) }
    }
}

@Composable
private fun MetricCard(label: String, value: String, accent: androidx.compose.ui.graphics.Color, modifier: Modifier = Modifier) {
    Surface(
        color = Panel2,
        shape = RoundedCornerShape(8.dp),
        modifier = modifier,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Text(value, color = accent, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text(label, color = Muted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun StateBadge(state: String) {
    val color = when (state.lowercase()) {
        "ready" -> Green
        "degraded" -> Amber
        "idle" -> Muted
        else -> Red
    }
    Surface(color = color.copy(alpha = 0.12f), shape = RoundedCornerShape(999.dp)) {
        Text(
            tStatus(state),
            color = color,
            fontSize = 10.sp,
            fontWeight = FontWeight.ExtraBold,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

@Composable
private fun RefreshButton(onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.size(32.dp)) {
        Icon(Icons.Default.Refresh, t("action.refresh"), tint = Muted, modifier = Modifier.size(18.dp))
    }
}

private fun stateColor(state: String) = when (state) {
    "ACTIVE" -> Green
    "ERROR" -> Red
    "TERMINATED" -> Red
    "INACTIVE" -> Muted
    else -> Dim
}

@Composable
fun inputColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor = Text,
    unfocusedTextColor = Text,
    focusedLabelColor = Cyan,
    unfocusedLabelColor = Muted,
    focusedBorderColor = Cyan,
    unfocusedBorderColor = Line,
    cursorColor = Cyan,
    focusedContainerColor = Panel2,
    unfocusedContainerColor = Panel2,
    focusedLeadingIconColor = Cyan,
    unfocusedLeadingIconColor = Muted,
)
