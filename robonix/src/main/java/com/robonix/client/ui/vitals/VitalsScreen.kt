package com.robonix.client.ui.vitals

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.robonix.client.data.model.ComponentHealthRow
import com.robonix.client.data.model.HardwareSnapshot
import com.robonix.client.data.model.ModuleHealth
import com.robonix.client.data.model.ProviderInfo
import com.robonix.client.data.model.RobotComponent
import com.robonix.client.data.model.RobotDescription
import com.robonix.client.domain.RobotVitalsMapper
import com.robonix.client.domain.RobotVitalsRepository
import com.robonix.client.domain.SystemRepository
import com.robonix.client.domain.VitalsRepository
import com.robonix.client.ui.i18n.t
import com.robonix.client.ui.i18n.tStatus
import com.robonix.client.ui.navigation.SharedViewModel
import com.robonix.client.ui.theme.*
import com.robonix.proto.vitalsstream.VitalsSnapshot
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

data class VitalsUiState(
    val description: RobotDescription? = null,
    val hardware: HardwareSnapshot? = null,
    val modules: List<ModuleHealth> = emptyList(),
    val providers: List<ProviderInfo> = emptyList(),
    val loaded: Boolean = false,
    val descriptionError: String? = null,
    val hardwareError: String? = null,
    val moduleError: String? = null,
    val selected: ModuleHealth? = null,
)

private data class TreeNode(
    val component: RobotComponent,
    val depth: Int,
    val children: List<TreeNode>,
)

@HiltViewModel
class VitalsViewModel @Inject constructor(
    private val vitalsRepository: VitalsRepository,
    private val robotVitalsRepository: RobotVitalsRepository,
    private val systemRepository: SystemRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(VitalsUiState())
    val uiState: StateFlow<VitalsUiState> = _uiState.asStateFlow()

    private var streamJob: Job? = null
    private var pollJob: Job? = null
    private var lastSnapshot: VitalsSnapshot? = null

    fun start(target: String) {
        if (target.isBlank()) return
        streamJob?.cancel()
        pollJob?.cancel()
        lastSnapshot = null

        loadDescription(target)
        loadModules(target)
        loadProviders(target)

        streamJob = viewModelScope.launch {
            var retry = 1_000L
            while (isActive) {
                try {
                    robotVitalsRepository.streamVitalsSnapshots(target).collect { snapshot ->
                        lastSnapshot = snapshot
                        val description = _uiState.value.description ?: return@collect
                        val hardware = RobotVitalsMapper.snapshotToHardware(snapshot, description)
                        _uiState.update { it.copy(hardware = hardware, hardwareError = null, loaded = true) }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    _uiState.update { it.copy(hardwareError = e.message) }
                    delay(retry)
                    retry = (retry * 2).coerceAtMost(15_000L)
                }
            }
        }

        pollJob = viewModelScope.launch {
            var tick = 0
            while (isActive) {
                delay(3_000L)
                tick++
                loadModules(target)
                // Providers come from the Atlas system snapshot (heartbeat-based);
                // refresh them less often than the module rows.
                if (tick % 2 == 0) loadProviders(target)
                if (tick % 10 == 0) loadDescription(target)
            }
        }
    }

    fun stop() {
        streamJob?.cancel(); streamJob = null
        pollJob?.cancel(); pollJob = null
    }

    fun assetForPath(path: String): ByteArray? = robotVitalsRepository.assetForPath(path)

    private fun loadDescription(target: String) {
        viewModelScope.launch {
            try {
                val description = robotVitalsRepository.loadRobotDescription(target)
                _uiState.update { state ->
                    val hardware = lastSnapshot
                        ?.let { RobotVitalsMapper.snapshotToHardware(it, description) }
                        ?: state.hardware
                    state.copy(description = description, hardware = hardware, descriptionError = null, loaded = true)
                }
            } catch (e: Exception) {
                _uiState.update { it.copy(descriptionError = e.message, loaded = true) }
            }
        }
    }

    private fun loadModules(target: String) {
        viewModelScope.launch {
            try {
                val modules = vitalsRepository.getModuleHealthSnapshot(target)
                _uiState.update { it.copy(modules = modules, moduleError = null) }
            } catch (e: Exception) {
                _uiState.update { it.copy(moduleError = e.message) }
            }
        }
    }

    private fun loadProviders(target: String) {
        viewModelScope.launch {
            try {
                val snapshot = systemRepository.getSystemSnapshot(target)
                // getSystemSnapshot swallows errors into snapshot.error; on error the
                // provider list is empty, so only overwrite when we got providers.
                if (snapshot.providers.isNotEmpty() || _uiState.value.providers.isEmpty()) {
                    _uiState.update { it.copy(providers = snapshot.providers) }
                }
            } catch (_: Exception) {
                // Keep the last known provider list on a transient failure.
            }
        }
    }

    fun select(module: ModuleHealth?) {
        _uiState.update { it.copy(selected = module) }
    }
}

@Composable
fun VitalsScreen(
    viewModel: VitalsViewModel = hiltViewModel(),
    sharedViewModel: SharedViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsState()
    val settings by sharedViewModel.settings.collectAsState()
    val endpoint = settings.atlasEndpoint

    DisposableEffect(endpoint) {
        viewModel.start(endpoint)
        onDispose { viewModel.stop() }
    }

    val description = state.description
    val hardware = state.hardware
    val highlights = remember(hardware) {
        hardware?.componentHealth?.associate { it.componentId to it.visualState } ?: emptyMap()
    }

    Column(modifier = Modifier.fillMaxSize()) {
        state.descriptionError?.let { ErrorBanner(it) }
        state.hardwareError?.let { ErrorBanner(it) }
        state.moduleError?.let { ErrorBanner(it) }

        if (description != null) {
            UrdfViewer(
                description = description,
                highlights = highlights,
                assetProvider = viewModel::assetForPath,
                modifier = Modifier.fillMaxWidth().height(300.dp),
            )
        }

        if (description == null && state.loaded) {
            Column(
                modifier = Modifier.fillMaxWidth().weight(1f).padding(48.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(Icons.Default.MonitorHeart, null, tint = Dim, modifier = Modifier.size(48.dp))
                Spacer(Modifier.height(12.dp))
                Text(t("vitals.empty"), color = Muted, fontSize = 13.sp)
            }
        } else {
            VitalsBody(
                description = description,
                hardware = hardware,
                modules = state.modules,
                providers = state.providers,
                viewModel = viewModel,
            )
        }
    }

    state.selected?.let { module ->
        ModuleDetailDialog(module) { viewModel.select(null) }
    }
}

@Composable
private fun VitalsBody(
    description: RobotDescription?,
    hardware: HardwareSnapshot?,
    modules: List<ModuleHealth>,
    providers: List<ProviderInfo>,
    viewModel: VitalsViewModel,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { SummaryCard(description, hardware) }
        if (description != null) {
            item { TopologyCard(description, hardware?.componentHealth.orEmpty()) }
        }
        if (hardware != null && hardware.signals.isNotEmpty()) {
            item { SignalsCard(hardware) }
        }
        if (modules.isNotEmpty() || providers.isNotEmpty()) {
            item { SoftwarePanel(modules, providers) { viewModel.select(it) } }
        }
        item { Spacer(Modifier.height(32.dp)) }
    }
}

@Composable
private fun ErrorBanner(message: String) {
    Surface(color = Red.copy(alpha = 0.1f), modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Default.ErrorOutline, null, tint = Red, modifier = Modifier.size(14.dp))
            Spacer(Modifier.width(6.dp))
            Text(message, color = Red, fontSize = 11.sp, modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun SummaryCard(description: RobotDescription?, hardware: HardwareSnapshot?) {
    Surface(color = Panel, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val overall = worstState(hardware?.componentHealth?.map { it.health }.orEmpty())
                Icon(
                    Icons.Default.MonitorHeart, null,
                    tint = stateColor(overall), modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    description?.displayName ?: t("nav.vitals"),
                    color = Text, fontSize = 14.sp, fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.width(8.dp))
                Text(tStatus(overall), color = stateColor(overall), fontSize = 12.sp, fontWeight = FontWeight.ExtraBold)
                Spacer(Modifier.weight(1f))
            }
            hardware?.power?.let { power ->
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.BatteryChargingFull, null, tint = Cyan, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "${power.socPercent}%  ${power.voltage}V${if (power.charging) " · " + t("vitals.charging") else ""}",
                        color = Muted, fontSize = 12.sp,
                    )
                }
            }
            if (description != null && description.components.size > 1) {
                Spacer(Modifier.height(6.dp))
                Text(t("vitals.components", description.components.size), color = Muted, fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun TopologyCard(description: RobotDescription, health: List<ComponentHealthRow>) {
    Surface(color = Panel, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text(t("vitals.topology"), color = Text, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            val healthById = health.associateBy { it.componentId }
            val roots = buildTree(description.components)
            roots.forEach { TreeNodeRow(it, healthById) }
        }
    }
}

@Composable
private fun TreeNodeRow(node: TreeNode, healthById: Map<String, ComponentHealthRow>) {
    val health = healthById[node.component.id]
    val state = health?.visualState ?: "unknown"
    Row(
        modifier = Modifier.fillMaxWidth().padding(start = (node.depth * 14).dp, top = 3.dp, bottom = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(stateColor(state)))
        Spacer(Modifier.width(8.dp))
        Text(
            node.component.label.ifBlank { node.component.id },
            color = Text, fontSize = 12.sp, modifier = Modifier.weight(1f),
        )
        Text(tStatus(state), color = stateColor(state), fontSize = 10.sp, fontWeight = FontWeight.Bold)
    }
    node.children.forEach { TreeNodeRow(it, healthById) }
}

@Composable
private fun SignalsCard(hardware: HardwareSnapshot) {
    Surface(color = Panel, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text(t("vitals.signals"), color = Text, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            hardware.signals.forEach { signal ->
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        signal.key.substringAfterLast('/'),
                        color = Muted, fontSize = 11.sp, modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        formatValue(signal.observedValue),
                        color = Text, fontSize = 11.sp,
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(tStatus(signal.visualState), color = stateColor(signal.visualState), fontSize = 10.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

private fun formatValue(value: Float): String =
    if (value == value.toLong().toFloat()) value.toLong().toString() else "%.2f".format(Locale.US, value)

private fun buildTree(components: List<RobotComponent>): List<TreeNode> {
    val byParent = components.groupBy { it.parentId }
    fun node(component: RobotComponent, depth: Int): TreeNode =
        TreeNode(component, depth, byParent[component.id].orEmpty().map { node(it, depth + 1) })
    return byParent[""].orEmpty().map { node(it, 0) }
}

private val stateSeverity = mapOf(
    "unknown" to 0, "ok" to 1, "stale" to 2, "warn" to 3, "error" to 4,
)

private fun worstState(states: List<String>): String =
    states.maxByOrNull { stateSeverity[it] ?: 0 } ?: "unknown"

private fun stateColor(state: String): Color = when (state) {
    "ok" -> Green
    "idle" -> Amber
    "warn" -> Amber
    "error" -> Red
    "stale" -> Dim
    else -> Dim
}

/**
 * Module/provider health word → severity. Mirrors the web client's ordering
 * (`unknown < ok < stale < warn < error`).
 */
private fun severityOf(health: String): Int = when (health) {
    "ok" -> 1
    "stale" -> 2
    "warn" -> 3
    "error" -> 4
    else -> 0
}

/**
 * Provider health derived from Atlas lifecycle state — mirrors the web client's
 * `vitals_transport.py::_provider_health` (ACTIVE = ok, ERROR = error,
 * TERMINATED = stale, INACTIVE/REGISTERED = warn, except idle skills are ok).
 */
private fun providerHealthOf(provider: ProviderInfo): String {
    val state = provider.state.uppercase()
    val kind = provider.kind.lowercase()
    if (kind == "skill" && state == "INACTIVE") return "ok"
    return when (state) {
        "ACTIVE" -> "ok"
        "ERROR" -> "error"
        "TERMINATED" -> "stale"
        "INACTIVE" -> "warn"
        "REGISTERED" -> "warn"
        else -> "unknown"
    }
}

@Composable
private fun SoftwarePanel(
    modules: List<ModuleHealth>,
    providers: List<ProviderInfo>,
    onModuleTap: (ModuleHealth) -> Unit,
) {
    val showModules = modules.isNotEmpty()
    val showProviders = providers.isNotEmpty()
    var view by rememberSaveable { mutableStateOf(if (showModules) "modules" else "providers") }
    // Never point at a tab whose data is missing (e.g. data cleared after rotation).
    val selected = if (view == "providers" && showProviders) "providers" else "modules"

    Surface(color = Panel, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(t("vitals.software"), color = Text, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.weight(1f))
            }
            Spacer(Modifier.height(8.dp))

            val segments = buildList {
                if (showModules) add("modules" to t("vitals.view.modules"))
                if (showProviders) add("providers" to t("vitals.view.providers"))
            }
            if (segments.size > 1) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(Panel2)
                        .padding(2.dp),
                ) {
                    segments.forEach { (key, label) ->
                        val active = selected == key
                        Surface(
                            color = if (active) Cyan.copy(alpha = 0.16f) else Color.Transparent,
                            shape = RoundedCornerShape(6.dp),
                            modifier = Modifier.weight(1f).clickable { view = key },
                        ) {
                            Text(
                                label,
                                color = if (active) Cyan else Muted,
                                fontSize = 12.sp,
                                fontWeight = if (active) FontWeight.Bold else FontWeight.Medium,
                                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                                modifier = Modifier.padding(vertical = 6.dp),
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
            }

            when (selected) {
                "modules" -> {
                    Text(
                        t("vitals.modules", modules.size),
                        color = Muted, fontSize = 10.sp,
                        modifier = Modifier.padding(bottom = 4.dp),
                    )
                    val sorted = modules.sortedWith(
                        compareByDescending<ModuleHealth> { severityOf(it.health) }
                            .thenBy { it.moduleId.ifBlank { it.moduleKey } },
                    )
                    sorted.forEach { module ->
                        SoftwareModuleRow(module) { onModuleTap(module) }
                        if (module != sorted.last()) Spacer(Modifier.height(4.dp))
                    }
                }
                else -> {
                    Text(
                        t("vitals.providers", providers.size),
                        color = Muted, fontSize = 10.sp,
                        modifier = Modifier.padding(bottom = 4.dp),
                    )
                    val sorted = providers.sortedWith(
                        compareByDescending<ProviderInfo> { severityOf(providerHealthOf(it)) }
                            .thenBy { it.id },
                    )
                    sorted.forEach { provider ->
                        ProviderRow(provider)
                        if (provider != sorted.last()) Spacer(Modifier.height(4.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun SoftwareModuleRow(module: ModuleHealth, onClick: () -> Unit) {
    val name = module.moduleId.ifBlank { module.moduleKey }
    val stateLine = listOfNotNull(
        tStatus(module.state).takeIf { module.state.isNotBlank() },
        module.reasonCode.takeIf { it.isNotBlank() },
    ).joinToString(" · ")
    Surface(
        color = Panel2,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(stateColor(module.health)))
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        name,
                        color = Text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                        maxLines = 1, modifier = Modifier.weight(1f, fill = false),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        tStatus(module.health),
                        color = stateColor(module.health), fontSize = 11.sp,
                        fontWeight = FontWeight.ExtraBold,
                    )
                }
                if (stateLine.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        stateLine,
                        color = Muted, fontSize = 10.sp,
                        maxLines = 1,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
            }
            if (module.ttlMs > 0) {
                Spacer(Modifier.width(8.dp))
                Text("${module.ttlMs}ms", color = Dim, fontSize = 9.sp)
            }
        }
    }
}

@Composable
private fun ProviderRow(provider: ProviderInfo) {
    val health = providerHealthOf(provider)
    val meta = listOfNotNull(
        provider.namespace.takeIf { it.isNotBlank() },
        provider.capabilities.size.takeIf { it > 0 }?.let { t("vitals.caps", it) },
    ).joinToString(" · ")
    Surface(
        color = Panel2,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(stateColor(health)))
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        provider.id,
                        color = Text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                        maxLines = 1, modifier = Modifier.weight(1f, fill = false),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        tStatus(health),
                        color = stateColor(health), fontSize = 11.sp,
                        fontWeight = FontWeight.ExtraBold,
                    )
                }
                val second = listOfNotNull(
                    tStatus(provider.state).takeIf { provider.state.isNotBlank() },
                    meta.takeIf { it.isNotBlank() },
                ).joinToString(" · ")
                if (second.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        second,
                        color = Muted, fontSize = 10.sp,
                        maxLines = 1, modifier = Modifier.weight(1f, fill = false),
                    )
                }
            }
        }
    }
}

@Composable
private fun ModuleDetailDialog(module: ModuleHealth, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(t("vitals.detail.title"), color = Text, fontSize = 16.sp) },
        text = {
            Column {
                DetailRow(t("vitals.key"), module.moduleKey)
                DetailRow(t("vitals.id"), module.moduleId.ifBlank { "-" })
                DetailRow(t("vitals.provider"), module.providerId.ifBlank { "-" })
                DetailRow(t("vitals.state"), tStatus(module.state))
                DetailRow(t("vitals.health"), tStatus(module.health))
                DetailRow(t("vitals.reason"), module.reasonCode.ifBlank { "-" })
                DetailRow(t("vitals.source"), module.source.ifBlank { "-" })
                DetailRow(
                    t("vitals.updated"),
                    if (module.receivedTsNs > 0)
                        SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(module.receivedTsNs / 1_000_000))
                    else "-",
                )
                if (module.detail.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    Text(t("vitals.detail"), color = Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                    Text(
                        module.detail,
                        color = Text, fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(t("action.close"), color = Cyan) }
        },
        containerColor = Panel,
    )
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = Muted, fontSize = 11.sp)
        Text(
            value, color = Text, fontSize = 11.sp, fontWeight = FontWeight.Medium,
            maxLines = 1, modifier = Modifier.weight(1f, fill = false),
        )
    }
}
