package com.robonix.client.ui.chat

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.activity.compose.BackHandler
import androidx.hilt.navigation.compose.hiltViewModel
import com.robonix.client.data.local.Conversation
import com.robonix.client.data.model.ChatMessage
import com.robonix.client.data.model.MessageRole
import com.robonix.client.ui.i18n.t
import com.robonix.client.ui.i18n.tStatus
import com.robonix.client.ui.navigation.SharedViewModel
import com.robonix.client.ui.settings.inputColors
import com.robonix.client.ui.theme.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    chatViewModel: ChatViewModel,
    sessionDrawerState: DrawerState,
    sharedViewModel: SharedViewModel = hiltViewModel(),
) {
    val state by chatViewModel.uiState.collectAsState()
    val settings by sharedViewModel.settings.collectAsState()
    val connectionState by sharedViewModel.connectionState.collectAsState()
    var inputText by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current

    // Sync settings between ViewModels
    LaunchedEffect(settings) {
        chatViewModel.updateSettings(settings)
    }

    // Auto-scroll to bottom on new messages
    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.size - 1)
        }
    }

    // ---- Session sidebar (drawer) ----
    // The drawer state is hoisted to the app bar (AppNavigation) so the
    // top-bar menu button can open this drawer; keep a local alias for
    // readability of the callbacks below.
    val sessions by chatViewModel.sessions.collectAsState()
    val drawerState = sessionDrawerState

    var showRename by remember { mutableStateOf(false) }
    var renameText by remember { mutableStateOf("") }
    var showClearConfirm by remember { mutableStateOf(false) }

    // System back closes the drawer first instead of leaving the screen.
    BackHandler(enabled = drawerState.isOpen) {
        scope.launch { drawerState.close() }
    }

    // ---- Busy / processing indicator with a live status line & elapsed time ----
    val busyActive = state.isBusy || (state.voiceActive && !state.isRecording)
    var busyStart by remember { mutableStateOf(0L) }
    var tickNow by remember { mutableStateOf(0L) }
    LaunchedEffect(busyActive) {
        if (busyActive) {
            busyStart = System.currentTimeMillis()
            tickNow = busyStart
            while (busyActive) {
                delay(500)
                tickNow = System.currentTimeMillis()
            }
        }
    }
    val elapsedSeconds = if (busyActive && busyStart > 0) (tickNow - busyStart) / 1000 else 0

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionSidebarDrawer(
                sessions = sessions,
                currentSessionId = state.sessionId,
                onPick = { id ->
                    chatViewModel.switchToSession(id)
                    scope.launch { drawerState.close() }
                },
                onDelete = { id -> chatViewModel.deleteSession(id) },
                onRename = { _, title ->
                    renameText = title
                    showRename = true
                },
                onClear = { showClearConfirm = true },
            )
        },
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .imePadding()
                .navigationBarsPadding(),
        ) {
            // Goal Panel
            AnimatedVisibility(visible = state.taskState != null) {
                state.taskState?.let { task ->
                    Surface(
                        color = Panel,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        shape = RoundedCornerShape(8.dp),
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Default.GpsFixed, null, tint = Amber, modifier = Modifier.size(14.dp))
                                Spacer(Modifier.width(6.dp))
                                Text(t("chat.goal"), color = Amber, fontSize = 10.sp, fontWeight = FontWeight.ExtraBold)
                            }
                            Spacer(Modifier.height(4.dp))
                            Text(
                                task.goal.ifBlank { t("chat.goal.waiting") },
                                color = Text,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Medium,
                            )
                            if (task.status.isNotBlank()) {
                                Text(
                                    tStatus(task.status),
                                    color = when {
                                        task.status.contains("running", ignoreCase = true) ||
                                        task.status.contains("executing", ignoreCase = true) ||
                                        task.status.contains("planning", ignoreCase = true) ||
                                        task.status.contains("in_progress", ignoreCase = true) -> Amber
                                        task.status.contains("done", ignoreCase = true) ||
                                        task.status.contains("completed", ignoreCase = true) -> Green
                                        task.status.contains("failed", ignoreCase = true) ||
                                        task.status.contains("error", ignoreCase = true) -> Red
                                        else -> Muted
                                    },
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            }
                        }
                    }
                }
            }

            // Collapsible event timeline
            if (state.timeline.isNotEmpty()) {
                var timelineExpanded by remember { mutableStateOf(false) }
                Surface(color = Panel2, modifier = Modifier.fillMaxWidth()) {
                    Column {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { timelineExpanded = !timelineExpanded }
                                .padding(horizontal = 12.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Icon(
                                if (timelineExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                null, modifier = Modifier.size(14.dp), tint = Dim,
                            )
                            Spacer(Modifier.width(4.dp))
                            Text(t("chat.events"), color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                            Spacer(Modifier.weight(1f))
                            Text("${state.timeline.size}", color = Dim, fontSize = 10.sp)
                        }
                        if (timelineExpanded) {
                            LazyColumn(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 120.dp)
                                    .padding(horizontal = 12.dp, vertical = 2.dp),
                            ) {
                                items(state.timeline.take(20)) { event ->
                                    Row(modifier = Modifier.padding(vertical = 1.dp)) {
                                        Text(
                                            event.timestamp, color = Dim, fontSize = 9.sp,
                                            fontFamily = FontFamily.Monospace,
                                        )
                                        Spacer(Modifier.width(6.dp))
                                        val dotColor = when (event.kind) {
                                            "error" -> Red; "result", "plan" -> Cyan
                                            "status" -> Green; "voice" -> Amber
                                            else -> Muted
                                        }
                                        Box(
                                            Modifier.size(4.dp).clip(CircleShape).background(dotColor)
                                                .align(Alignment.CenterVertically),
                                        )
                                        Spacer(Modifier.width(4.dp))
                                        Text(
                                            event.text, color = Text, fontSize = 10.sp,
                                            maxLines = 1, modifier = Modifier.weight(1f),
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Messages
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp),
                state = listState,
                verticalArrangement = Arrangement.spacedBy(6.dp),
                contentPadding = PaddingValues(vertical = 8.dp),
            ) {
                if (state.messages.isEmpty()) {
                    item {
                        EmptyChatPlaceholder(
                            isConnected = connectionState.isOnline,
                            settings.atlasEndpoint,
                        )
                    }
                }
                items(state.messages, key = { it.id }) { message ->
                    MessageBubble(message)
                }
                // Processing indicator — spinner + live status + elapsed; disappears
                // with the turn (never persisted into history).
                if (busyActive) {
                    item {
                        WorkingIndicator(
                            liveStatus = state.liveStatus,
                            elapsedSeconds = elapsedSeconds,
                        )
                    }
                }
            }

            // TTS speaking indicator — pulsing speaker while the robot talks
            AnimatedVisibility(visible = state.ttsPlaying) {
                Surface(color = Cyan.copy(alpha = 0.08f), modifier = Modifier.fillMaxWidth()) {
                    Row(
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TtsPulseIcon()
                        Spacer(Modifier.width(8.dp))
                        Text(t("chat.tts.speaking"), color = Cyan, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    }
                }
            }

            // Composer
            ComposerBar(
                inputText = inputText,
                onInputChange = { inputText = it },
                onSend = {
                    val text = inputText.trim()
                    if (text.isNotBlank()) {
                        chatViewModel.sendTask(text)
                        inputText = ""
                        focusManager.clearFocus()
                    }
                },
                onStop = { chatViewModel.stopTask() },
                onVoiceStart = {
                    val isSteer = state.isBusy
                    chatViewModel.startVoice(isSteer)
                },
                onVoiceStop = { chatViewModel.onMicReleased() },
                isBusy = state.isBusy,
                isRecording = state.isRecording,
                isProcessing = chatViewModel.isVoiceProcessing,
                characterLimit = 2000,
            )
        }
    }

    // Rename dialog
    if (showRename) {
        AlertDialog(
            onDismissRequest = { showRename = false },
            title = { Text(t("chat.rename.title"), color = Text) },
            text = {
                OutlinedTextField(
                    value = renameText, onValueChange = { renameText = it },
                    singleLine = true,
                    placeholder = { Text(t("chat.rename.placeholder"), color = Dim) },
                    colors = inputColors(),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    chatViewModel.updateSession(state.sessionId, renameText)
                    showRename = false
                }) { Text(t("action.save"), color = Cyan) }
            },
            dismissButton = {
                TextButton(onClick = { showRename = false }) { Text(t("action.cancel"), color = Muted) }
            },
            containerColor = Panel,
        )
    }

    // Clear-all confirmation (triggered from the sidebar)
    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text(t("chat.history.clear.title"), color = Text) },
            text = { Text(t("chat.history.clear.text"), color = Muted, fontSize = 13.sp) },
            confirmButton = {
                TextButton(onClick = {
                    chatViewModel.clearAllSessions()
                    showClearConfirm = false
                    scope.launch { drawerState.close() }
                }) { Text(t("action.confirm"), color = Red, fontWeight = FontWeight.Bold) }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirm = false }) { Text(t("action.cancel"), color = Muted) }
            },
            containerColor = Panel,
        )
    }
}

/** Left drawer listing historical conversations with select / delete / clear-all. */
@Composable
private fun SessionSidebarDrawer(
    sessions: List<Conversation>,
    currentSessionId: String,
    onPick: (String) -> Unit,
    onDelete: (String) -> Unit,
    onRename: (String, String) -> Unit,
    onClear: () -> Unit,
) {
    ModalDrawerSheet(
        modifier = Modifier.width(320.dp),
        drawerContainerColor = Panel,
        drawerContentColor = Text,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    t("chat.history.title"),
                    color = Text, fontSize = 16.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                if (sessions.isNotEmpty()) {
                    TextButton(onClick = onClear) {
                        Text(t("chat.history.clear"), color = Red, fontSize = 12.sp)
                    }
                }
            }
            Spacer(Modifier.height(12.dp))

            if (sessions.isEmpty()) {
                Text(
                    t("chat.history.empty"),
                    color = Muted, fontSize = 13.sp,
                    modifier = Modifier.weight(1f),
                )
            } else {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                ) {
                    sessions.forEach { session ->
                        val isActive = session.id == currentSessionId
                        Surface(
                            color = if (isActive) Cyan.copy(alpha = 0.08f) else Panel3,
                            shape = RoundedCornerShape(8.dp),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(
                                    modifier = Modifier
                                        .weight(1f)
                                        .clickable { onPick(session.id) }
                                        .padding(horizontal = 12.dp, vertical = 10.dp),
                                ) {
                                    Text(
                                        session.title.ifBlank { t("chat.untitled") },
                                        color = if (isActive) Cyan else Text,
                                        fontSize = 14.sp,
                                        fontWeight = if (isActive) FontWeight.Bold else FontWeight.Normal,
                                        maxLines = 1,
                                    )
                                    Text(
                                        java.text.SimpleDateFormat("MM/dd HH:mm", java.util.Locale.getDefault())
                                            .format(java.util.Date(session.updatedAt)),
                                        color = Dim,
                                        fontSize = 11.sp,
                                    )
                                }
                                if (isActive) {
                                    Surface(color = Cyan.copy(alpha = 0.15f), shape = RoundedCornerShape(999.dp)) {
                                        Text(
                                            t("chat.active.badge"), color = Cyan, fontSize = 10.sp,
                                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                                        )
                                    }
                                    // Rename only makes sense for the session you're in.
                                    IconButton(onClick = { onRename(session.id, session.title) }) {
                                        Icon(Icons.Default.Edit, t("chat.rename.title"), tint = Dim, modifier = Modifier.size(18.dp))
                                    }
                                }
                                IconButton(onClick = { onDelete(session.id) }) {
                                    Icon(Icons.Default.Delete, t("action.delete"), tint = Dim, modifier = Modifier.size(18.dp))
                                }
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun EmptyChatPlaceholder(isConnected: Boolean, endpoint: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            if (isConnected) Icons.Default.Chat else Icons.Default.CloudOff,
            contentDescription = null,
            tint = if (isConnected) Dim else Red.copy(alpha = 0.4f),
            modifier = Modifier.size(48.dp),
        )
        Spacer(Modifier.height(12.dp))
        Text(
            if (isConnected) t("chat.empty.ready")
            else if (endpoint.isBlank()) t("chat.empty.configure")
            else tStatus("OFFLINE"),
            color = Muted,
            fontSize = 13.sp,
            textAlign = TextAlign.Center,
        )
    }
}

/**
 * Processing indicator appended while a turn is in flight. Shows a spinner plus
 * the most recent live status on the line below; the whole block disappears
 * when the turn ends, so it never pollutes the saved conversation.
 */
@Composable
private fun WorkingIndicator(liveStatus: String?, elapsedSeconds: Long) {
    Column(
        modifier = Modifier
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(AgentBg)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(
                modifier = Modifier.size(12.dp),
                strokeWidth = 1.5.dp,
                color = Cyan,
            )
            Spacer(Modifier.width(8.dp))
            Text(t("chat.typing"), color = Muted, fontSize = 12.sp)
            if (elapsedSeconds >= 1) {
                Spacer(Modifier.width(8.dp))
                Text(t("chat.wait.sec", elapsedSeconds), color = Dim, fontSize = 10.sp)
            }
        }
        if (!liveStatus.isNullOrBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                liveStatus,
                color = Cyan, fontSize = 11.sp, fontWeight = FontWeight.Medium,
            )
        }
    }
}

/** Pulsing speaker icon for the TTS speaking bar (device-safe animation). */
@Composable
private fun TtsPulseIcon() {
    val alpha by rememberInfiniteTransition(label = "tts").animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(500),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "ttsAlpha",
    )
    Icon(
        Icons.Default.VolumeUp,
        contentDescription = null,
        tint = Cyan.copy(alpha = alpha),
        modifier = Modifier.size(16.dp),
    )
}

@Composable
fun MessageBubble(message: ChatMessage) {
    val bgColor: androidx.compose.ui.graphics.Color
    val maxWidthFraction: Float
    val horizontalArrangement: Arrangement.Horizontal
    when (message.role) {
        MessageRole.User -> { bgColor = UserBg; maxWidthFraction = 0.85f; horizontalArrangement = Arrangement.End }
        MessageRole.Agent -> { bgColor = AgentBg; maxWidthFraction = 0.92f; horizontalArrangement = Arrangement.Start }
        MessageRole.Error -> { bgColor = Red.copy(alpha = 0.10f); maxWidthFraction = 0.95f; horizontalArrangement = Arrangement.Center }
        MessageRole.Status -> { bgColor = Panel2.copy(alpha = 0.5f); maxWidthFraction = 0.95f; horizontalArrangement = Arrangement.Center }
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = horizontalArrangement,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth(maxWidthFraction)
                .padding(horizontal = 4.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(bgColor)
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            if (message.meta.isNotBlank()) {
                Text(
                    message.meta.uppercase(),
                    color = if (message.role == MessageRole.User) Blue
                    else if (message.role == MessageRole.Agent) Green
                    else Muted,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.ExtraBold,
                    letterSpacing = 0.5.sp,
                )
                Spacer(Modifier.height(2.dp))
            }
            Text(
                message.text,
                color = Text,
                fontSize = 13.sp,
                lineHeight = 19.sp,
            )
            if (message.timestamp > 0) {
                Spacer(Modifier.height(2.dp))
                Text(
                    SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(message.timestamp)),
                    color = Dim,
                    fontSize = 9.sp,
                )
            }
        }
    }
}

@Composable
fun ComposerBar(
    inputText: String,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onVoiceStart: () -> Unit,
    onVoiceStop: () -> Unit,
    onVoiceForceStop: () -> Unit = {},
    isBusy: Boolean,
    isRecording: Boolean,
    isProcessing: Boolean = false,
    characterLimit: Int = 2000,
) {
    Surface(
        color = Panel,
        shadowElevation = 8.dp,
    ) {
        Column(modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.Bottom,
            ) {
                // Voice button — press-and-hold to talk
                var held by remember { mutableStateOf(false) }
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .pointerInput(Unit) {
                            awaitPointerEventScope {
                                while (true) {
                                    // Wait for a finger-down in the Initial pass so we
                                    // intercept before any parent (LazyColumn, etc.)
                                    val event = awaitPointerEvent(PointerEventPass.Initial)
                                    val pressed = event.changes.any { it.pressed }
                                    if (pressed && !held) {
                                        event.changes.forEach { it.consume() }
                                        held = true
                                        onVoiceStart()
                                    }
                                    val released = event.changes.any { !it.pressed && it.previousPressed }
                                    if (released && held) {
                                        event.changes.forEach { if (!it.pressed) it.consume() }
                                        held = false
                                        onVoiceStop()
                                    }
                                }
                            }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Default.Mic,
                        contentDescription = t("composer.voice"),
                        tint = when {
                            held || isRecording -> Red
                            isProcessing -> Amber
                            else -> Muted
                        },
                        modifier = Modifier.size(24.dp),
                    )
                }

                // Text input
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { if (it.length <= characterLimit) onInputChange(it) },
                    placeholder = {
                        Text(
                            if (isBusy) t("composer.placeholder.busy") else t("composer.placeholder"),
                            color = Dim,
                            fontSize = 14.sp,
                        )
                    },
                    modifier = Modifier.weight(1f),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Text,
                        unfocusedTextColor = Text,
                        focusedBorderColor = if (isBusy) Amber else Cyan,
                        unfocusedBorderColor = Line,
                        cursorColor = Cyan,
                        focusedContainerColor = Panel2,
                        unfocusedContainerColor = Panel2,
                    ),
                    shape = RoundedCornerShape(10.dp),
                    maxLines = 4,
                    textStyle = androidx.compose.ui.text.TextStyle(fontSize = 14.sp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { onSend() }),
                )

                Spacer(Modifier.width(4.dp))

                // Stop button — compact, icon-only when busy
                AnimatedVisibility(visible = isBusy) {
                    IconButton(
                        onClick = onStop,
                        modifier = Modifier.size(44.dp),
                    ) {
                        Icon(
                            Icons.Default.Stop, t("action.stop"),
                            tint = Red,
                            modifier = Modifier.size(22.dp),
                        )
                    }
                }

                // Send / Steer button
                Button(
                    onClick = onSend,
                    enabled = inputText.isNotBlank(),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isBusy) Amber else Blue,
                        contentColor = Bg,
                        disabledContainerColor = Panel3,
                        disabledContentColor = Dim,
                    ),
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.height(48.dp),
                    contentPadding = PaddingValues(horizontal = 16.dp),
                ) {
                    Icon(
                        Icons.Default.Send,
                        if (isBusy) t("action.steer") else t("action.send"),
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(
                        if (isBusy) t("action.steer") else t("action.send"),
                        fontWeight = FontWeight.ExtraBold,
                        fontSize = 13.sp,
                    )
                }
            }

            // Character counter and status
            if (inputText.length > characterLimit * 0.8f || isRecording || isProcessing) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    if (isRecording) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(8.dp).clip(CircleShape).background(Red))
                            Spacer(Modifier.width(4.dp))
                            Text(t("composer.recording"), color = Red, fontSize = 11.sp)
                        }
                    } else if (isProcessing) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(8.dp).clip(CircleShape).background(Amber))
                            Spacer(Modifier.width(4.dp))
                            Text(t("composer.processing"), color = Amber, fontSize = 11.sp)
                        }
                    } else {
                        Spacer(Modifier.width(1.dp))
                    }
                    if (inputText.length > characterLimit * 0.8f) {
                        Text("${inputText.length}/$characterLimit",
                            color = if (inputText.length >= characterLimit) Red else Muted,
                            fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
    }
}
