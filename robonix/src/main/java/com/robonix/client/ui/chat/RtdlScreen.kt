package com.robonix.client.ui.chat

import android.util.Log
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.robonix.client.data.model.*
import com.robonix.client.domain.RtdlStateHolder
import com.robonix.client.domain.SystemRepository
import com.robonix.client.ui.i18n.t
import com.robonix.client.ui.i18n.tStatus
import com.robonix.client.ui.navigation.SharedViewModel
import com.robonix.client.ui.theme.*
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

private const val TAG = "RobonixRTDL"

data class RtdlUiState(
    val planRecords: List<PlanRecord> = emptyList(),
    val activePlanIds: Set<String> = emptySet(),
    val executorPlans: List<ExecutorPlan> = emptyList(),
    val executorPlansReady: Boolean = false,
    val isRefreshing: Boolean = false,
    val selectedTab: Int = 0,
    val selectedNode: RtdlNode? = null,
    val selectedNodeState: String = "",
    val plan: RtdlPlan? = null,
    val nodeStates: Map<Int, RtdlNodeState> = emptyMap(),
    val error: String? = null,
)

@HiltViewModel
class RtdlViewModel @Inject constructor(
    private val systemRepository: SystemRepository,
    private val rtdlState: RtdlStateHolder,
) : ViewModel() {

    private val _uiState = MutableStateFlow(RtdlUiState())
    val uiState: StateFlow<RtdlUiState> = _uiState.asStateFlow()

    init {
        Log.w(TAG, "RtdlViewModel created, starting collectors")
        // Sync live plan data from ChatViewModel (via RtdlStateHolder)
        viewModelScope.launch {
            rtdlState.livePlan.collect { plan ->
                Log.w(TAG, "livePlan collector: plan=${plan?.planId}, nodes=${plan?.nodes?.size}")
                _uiState.update { it.copy(plan = plan) }
            }
        }
        viewModelScope.launch {
            rtdlState.liveNodeStates.collect { ns ->
                Log.w(TAG, "liveNodeStates collector: ${ns.size} entries")
                _uiState.update { it.copy(nodeStates = ns) }
            }
        }
        viewModelScope.launch {
            rtdlState.planRecords.collect { records ->
                Log.w(TAG, "planRecords collector: ${records.size} records")
                _uiState.update { it.copy(planRecords = records) }
            }
        }
    }

    fun refreshActivePlans(target: String) {
        Log.w(TAG, "refreshActivePlans called, target=$target")
        if (target.isBlank()) {
            Log.w(TAG, "refreshActivePlans: target blank, skipping")
            return
        }
        _uiState.update { it.copy(isRefreshing = true, error = null) }
        viewModelScope.launch {
            try {
                Log.w(TAG, "Calling systemRepository.getActivePlans($target)")
                val plans = systemRepository.getActivePlans(target)
                Log.w(TAG, "getActivePlans returned ${plans.size} plans")
                _uiState.update {
                    it.copy(
                        executorPlans = plans,
                        executorPlansReady = true,
                        activePlanIds = plans.map { p -> p.planId }.toSet(),
                        isRefreshing = false,
                    )
                }
            } catch (e: Exception) {
                Log.e(TAG, "getActivePlans failed", e)
                _uiState.update {
                    it.copy(
                        executorPlansReady = false,
                        isRefreshing = false,
                        error = e.message,
                    )
                }
            }
        }
    }

    fun selectNode(node: RtdlNode?, state: String = "") {
        _uiState.update { it.copy(selectedNode = node, selectedNodeState = state) }
    }

    fun selectTab(index: Int) {
        _uiState.update { it.copy(selectedTab = index, selectedNode = null) }
    }
}

@Composable
fun RtdlScreen(
    viewModel: RtdlViewModel = hiltViewModel(),
    sharedViewModel: SharedViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsState()
    val settings by sharedViewModel.settings.collectAsState()

    LaunchedEffect(settings.atlasEndpoint) {
        Log.w(TAG, "RtdlScreen entered, atlasEndpoint=${settings.atlasEndpoint}")
        viewModel.refreshActivePlans(settings.atlasEndpoint)
        // Poll active plans while the tab is visible (web: 2s → mobile: 5s to
        // save battery/radio). delay() is cancellable, so leaving the screen
        // cancels the loop automatically.
        while (true) {
            kotlinx.coroutines.delay(5000)
            val ep = sharedViewModel.settings.value.atlasEndpoint
            if (ep.isNotBlank()) viewModel.refreshActivePlans(ep)
        }
    }

    RtdlContent(state, viewModel, sharedViewModel)
}

@Composable
private fun RtdlContent(
    state: RtdlUiState,
    viewModel: RtdlViewModel,
    sharedViewModel: SharedViewModel,
) {
    Column(modifier = Modifier.fillMaxSize()) {
        // Tabs
        TabRow(
            selectedTabIndex = state.selectedTab,
            containerColor = Panel,
            contentColor = Cyan,
            indicator = { tabPositions ->
                if (state.selectedTab < tabPositions.size) {
                    val pos = tabPositions[state.selectedTab]
                    Box(
                        Modifier
                            .offset(x = pos.left, y = 45.dp)
                            .width(pos.width)
                            .height(3.dp)
                            .background(Cyan),
                    )
                }
            },
        ) {
            Tab(
                selected = state.selectedTab == 0,
                onClick = { viewModel.selectTab(0) },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.PlayArrow, null, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(4.dp))
                        Text(t("rtdl.tab.active", state.executorPlans.size), fontSize = 13.sp)
                    }
                },
            )
            Tab(
                selected = state.selectedTab == 1,
                onClick = { viewModel.selectTab(1) },
                text = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.History, null, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(4.dp))
                        val historyCount = state.planRecords.size
                        Text(t("rtdl.tab.history", historyCount), fontSize = 13.sp)
                    }
                },
            )
        }

        when (state.selectedTab) {
            0 -> ActivePlansTab(state, viewModel)
            1 -> PlanHistoryTab(state, viewModel)
        }

        // Node detail bottom sheet
        state.selectedNode?.let { node ->
            NodeDetailSheet(
                node = node,
                status = state.selectedNodeState,
                nodeState = state.nodeStates[node.index],
                onDismiss = { viewModel.selectNode(null) },
            )
        }
    }
}

@Composable
private fun ActivePlansTab(state: RtdlUiState, viewModel: RtdlViewModel) {
    Box(modifier = Modifier.fillMaxSize()) {
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (state.isRefreshing) {
                item {
                    // Use a simple animated bar instead of Material3 LinearProgressIndicator
                    // which triggers a NoSuchMethodError on KeyframesSpecConfig.at()
                    // due to a Compose BOM version incompatibility on this device.
                    SimpleProgressBar()
                }
            }
            if (state.error != null) {
                item {
                    ErrorBanner(state.error ?: "")
                }
            }
            if (!state.executorPlansReady && !state.isRefreshing) {
                item {
                    EmptyState(t("rtdl.loading"), Icons.Default.HourglassEmpty)
                }
            } else if (state.executorPlansReady && state.executorPlans.isEmpty()) {
                item {
                    EmptyState(t("rtdl.active.empty"), Icons.Default.Inbox)
                }
            }
            items(state.executorPlans, key = { it.planId }) { plan ->
                ExecutorPlanCard(plan)
            }
            // Behavior tree for the latest active plan (from Chat events)
            val activeRecord = state.planRecords.firstOrNull()
            if (activeRecord != null) {
                item {
                    SectionHeader(t("rtdl.live.tree"))
                }
                item {
                    SafeBehaviorTree(
                        plan = activeRecord.plan,
                        nodeStates = activeRecord.nodeStates,
                        onNodeSelect = { node, s -> viewModel.selectNode(node, s) },
                    )
                }
            }
        }
    }
}

@Composable
private fun PlanHistoryTab(state: RtdlUiState, viewModel: RtdlViewModel) {
    val historyRecords = state.planRecords

    if (historyRecords.isEmpty()) {
        EmptyState(t("rtdl.history.empty"), Icons.Default.Inbox)
        return
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(historyRecords, key = { it.key }) { record ->
            SafeBehaviorTree(
                plan = record.plan,
                nodeStates = record.nodeStates,
                onNodeSelect = { node, s -> viewModel.selectNode(node, s) },
            )
        }
    }
}

/**
 * Wrapper around BehaviorTreeCard that logs tree data for debugging.
 * The actual crash protection is inside TreeNodeRow (depth limit + cycle detection).
 */
@Composable
private fun SafeBehaviorTree(
    plan: RtdlPlan,
    nodeStates: Map<Int, RtdlNodeState>,
    onNodeSelect: (RtdlNode, String) -> Unit,
) {
    Log.w(TAG, "SafeBehaviorTree: planId=${plan.planId} round=${plan.round} nodes=${plan.nodes.size}")
    BehaviorTreeCard(plan, nodeStates, onNodeSelect)
}

@Composable
private fun BehaviorTreeCard(
    plan: RtdlPlan,
    nodeStates: Map<Int, RtdlNodeState>,
    onNodeSelect: (RtdlNode, String) -> Unit,
) {
    val nodes = plan.nodes
    if (nodes.isEmpty()) return

    Log.w(TAG, "BehaviorTreeCard: planId=${plan.planId}, round=${plan.round}, nodes=${nodes.size}")

    Card(
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    t("rtdl.plan", plan.planId.ifBlank { "-" }),
                    color = Text,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Surface(color = Cyan.copy(alpha = 0.1f), shape = RoundedCornerShape(999.dp)) {
                    Text(
                        t("rtdl.round", plan.round),
                        color = Cyan,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                    )
                }
            }

            Spacer(Modifier.height(12.dp))
            val byIndex = remember(nodes) { nodes.associateBy { it.index } }
            val childSet = remember(nodes) { nodes.flatMap { it.children }.toSet() }

            val roots = remember(nodes, childSet) {
                val r = mutableListOf<RtdlNode>()
                if (plan.rootIndex in byIndex) r.add(byIndex[plan.rootIndex]!!)
                nodes.forEach { if (it.index !in childSet && it !in r) r.add(it) }
                if (r.isEmpty() && nodes.isNotEmpty()) r.add(nodes.first())
                Log.w(TAG, "Tree roots: ${r.map { it.index }}, rootIndex=${plan.rootIndex}")
                r
            }

            roots.forEach { root ->
                TreeNodeRow(root, byIndex, nodeStates, 0, onNodeSelect)
                if (root != roots.last()) Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
private fun TreeNodeRow(
    node: RtdlNode,
    byIndex: Map<Int, RtdlNode>,
    nodeStates: Map<Int, RtdlNodeState>,
    depth: Int,
    onNodeSelect: (RtdlNode, String) -> Unit,
    visited: MutableSet<Int> = mutableSetOf(),
) {
    // Safety: guard against cycles and excessive depth (StackOverflowError)
    if (depth > 64) {
        Log.w(TAG, "TreeNodeRow: max depth reached for node #${node.index}")
        return
    }
    if (!visited.add(node.index)) {
        Log.w(TAG, "TreeNodeRow: cycle detected at node #${node.index}")
        return
    }

    val state = nodeStates[node.index]
    val status = state?.state ?: "PENDING"
    val statusColor = when (status) {
        "SUCCEEDED" -> Green
        "RUNNING" -> Amber
        "FAILED", "CANCELED", "TIMEOUT" -> Red
        else -> Muted
    }
    val nodeBg = when (status) {
        "SUCCEEDED" -> Green.copy(alpha = 0.06f)
        "RUNNING" -> Amber.copy(alpha = 0.08f)
        "FAILED" -> Red.copy(alpha = 0.06f)
        else -> Panel2
    }
    val label = node.call?.name
        ?: node.opId.ifBlank { node.description.ifBlank { node.kind } }
    val provider = node.call?.let { "${it.providerId}.${it.name}" } ?: ""

    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = (depth * 18).dp)
                .clip(RoundedCornerShape(6.dp))
                .background(nodeBg)
                .clickable { onNodeSelect(node, status) }
                .padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    "#${node.index} ${label.take(30)}",
                    color = Text,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                if (provider.isNotBlank()) {
                    Text(
                        provider,
                        color = Cyan,
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1,
                    )
                }
            }
            Surface(
                color = statusColor.copy(alpha = 0.12f),
                shape = RoundedCornerShape(999.dp),
            ) {
                Text(
                    tStatus(status),
                    color = statusColor,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.ExtraBold,
                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
        }
        // Children — guarded by visited set + depth cap above
        node.children.forEach { childIdx ->
            byIndex[childIdx]?.let { child ->
                TreeNodeRow(child, byIndex, nodeStates, depth + 1, onNodeSelect, visited)
            }
        }
    }
}

@Composable
private fun ExecutorPlanCard(plan: ExecutorPlan) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Panel2),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        plan.description.ifBlank { t("rtdl.plan", plan.planId) },
                        color = Text,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        t("rtdl.plan.meta", plan.planId, plan.opCount),
                        color = Muted,
                        fontSize = 11.sp,
                    )
                }
                Surface(
                    color = if (plan.cancelled) Red.copy(alpha = 0.12f) else Green.copy(alpha = 0.1f),
                    shape = RoundedCornerShape(999.dp),
                ) {
                    Text(
                        if (plan.cancelled) tStatus("CANCELING") else tStatus("RUNNING"),
                        color = if (plan.cancelled) Red else Green,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.ExtraBold,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                    )
                }
            }
            Spacer(Modifier.height(10.dp))

            plan.ops.forEach { op ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .background(Bg)
                        .padding(8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(op.description.ifBlank { t("rtdl.op", op.opId) }, color = Text, fontSize = 12.sp)
                        Text("${op.providerId} · ${op.contractId}", color = Cyan, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                    Text(
                        tStatus(op.state),
                        color = when (op.state.lowercase()) {
                            "running" -> Amber; "succeeded", "success", "done" -> Green
                            "failed", "error" -> Red; else -> Muted
                        },
                        fontSize = 10.sp,
                        fontWeight = FontWeight.ExtraBold,
                    )
                }
                if (op != plan.ops.last()) Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
private fun NodeDetailSheet(
    node: RtdlNode,
    status: String,
    nodeState: RtdlNodeState?,
    onDismiss: () -> Unit,
) {
    Surface(
        color = Panel3,
        shape = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp),
        shadowElevation = 16.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(t("rtdl.node.detail"), color = Amber, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                    Icon(Icons.Default.Close, t("action.close"), tint = Muted, modifier = Modifier.size(20.dp))
                }
            }
            Spacer(Modifier.height(12.dp))

            DetailRow(t("rtdl.node"), "#${node.index} ${node.call?.name ?: node.opId.ifBlank { node.kind }}")
            DetailRow(t("rtdl.node.provider"), node.call?.providerId ?: "-")
            DetailRow(t("rtdl.node.contract"), node.call?.contractId ?: "-")
            DetailRow(t("rtdl.node.status"), status)

            Spacer(Modifier.height(8.dp))
            Text(t("rtdl.node.args"), color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            Text(
                node.call?.args?.toString() ?: "{}",
                color = Text,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(6.dp))
                    .background(Panel2)
                    .padding(10.dp),
            )

            if (nodeState?.leafResult != null) {
                Spacer(Modifier.height(8.dp))
                Text(t("rtdl.node.result"), color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Text(
                    "success=${nodeState.leafResult.success}\n${nodeState.leafResult.output.ifBlank { nodeState.leafResult.error }}",
                    color = Text,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .background(Panel2)
                        .padding(10.dp),
                )
            }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = Muted, fontSize = 11.sp)
        Text(value, color = Text, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        color = Text,
        fontSize = 14.sp,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
    )
}

@Composable
private fun EmptyState(message: String, icon: androidx.compose.ui.graphics.vector.ImageVector) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, null, tint = Dim, modifier = Modifier.size(48.dp))
        Spacer(Modifier.height(12.dp))
        Text(message, color = Muted, fontSize = 13.sp)
    }
}

/**
 * A simple indeterminate progress bar that avoids Material3's LinearProgressIndicator
 * which crashes with NoSuchMethodError on KeyframesSpecConfig.at() due to a Compose
 * BOM version mismatch on this device.
 */
@Composable
private fun SimpleProgressBar() {
    // Pulsing alpha animation — uses only basic Compose animate APIs available in all versions
    val alpha by rememberInfiniteTransition(label = "pulse").animateFloat(
        initialValue = 0.3f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "alpha",
    )
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(3.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(Cyan.copy(alpha = alpha)),
    )
}

@Composable
private fun ErrorBanner(error: String) {
    Surface(
        color = Red.copy(alpha = 0.1f),
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.ErrorOutline, null, tint = Red, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
            Text(error, color = Red, fontSize = 12.sp)
        }
    }
}
