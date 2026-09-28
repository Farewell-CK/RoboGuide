package com.robonix.client.domain

import android.util.Log
import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.ExecutorClient
import com.robonix.client.data.model.ExecutorPlan
import com.robonix.client.data.model.SystemSnapshot
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SystemRepository @Inject constructor(
    private val atlasClient: AtlasClient,
    private val executorClient: ExecutorClient,
) {
    companion object {
        // Contract IDs — must match the Robonix backend exactly
        const val CONTRACT_EXECUTOR_LIST_ACTIVE = "robonix/system/executor/list_active_plans"
        const val DEFAULT_EXECUTOR_PORT = 50061
    }

    suspend fun getSystemSnapshot(target: String): SystemSnapshot =
        atlasClient.getSystemSnapshot(target)

    /**
     * Discover Executor's endpoint through Atlas, fall back to {atlas_host}:50061
     * on discovery failure (matching the web client pattern).
     */
    suspend fun getActivePlans(atlasEndpoint: String): List<ExecutorPlan> {
        Log.w("RobonixRepo", "getActivePlans: atlas=$atlasEndpoint")
        val executorEndpoint = try {
            val ep = atlasClient.discoverEndpoint(atlasEndpoint, CONTRACT_EXECUTOR_LIST_ACTIVE)
            Log.w("RobonixRepo", "getActivePlans: discovered executor at $ep")
            ep
        } catch (e: Exception) {
            val host = atlasEndpoint.substringBeforeLast(":")
            val fallback = if (host.isNotBlank()) "$host:$DEFAULT_EXECUTOR_PORT"
            else throw RuntimeException("Cannot resolve Executor endpoint from $atlasEndpoint")
            Log.w("RobonixRepo", "getActivePlans: discovery failed, fallback=$fallback", e)
            fallback
        }
        val plans = executorClient.listActivePlans(executorEndpoint)
        Log.w("RobonixRepo", "getActivePlans: got ${plans.size} plans from $executorEndpoint")
        return plans
    }
}
