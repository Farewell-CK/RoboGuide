package com.robonix.client.domain

import android.util.Log
import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.GrpcChannelProvider
import com.robonix.client.data.model.ModuleHealth
import com.robonix.proto.vitals.GetModuleHealthSnapshot_Request
import com.robonix.proto.vitals.GetModuleHealthSnapshot_Response
import io.grpc.MethodDescriptor
import io.grpc.protobuf.ProtoUtils
import io.grpc.stub.ClientCalls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Vitals 2D health center — module health snapshots via the
 * `robonix/system/vitals/modules/get` contract (web client's vitals panel).
 */
@Singleton
class VitalsRepository @Inject constructor(
    private val atlasClient: AtlasClient,
    private val channelProvider: GrpcChannelProvider,
) {
    companion object {
        const val CONTRACT_VITALS_MODULES_GET = "robonix/system/vitals/modules/get"
    }

    private val MODULE_HEALTH_METHOD: MethodDescriptor<GetModuleHealthSnapshot_Request, GetModuleHealthSnapshot_Response> =
        MethodDescriptor.newBuilder<GetModuleHealthSnapshot_Request, GetModuleHealthSnapshot_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemVitalsModulesGet/GetModuleHealthSnapshot")
            .setRequestMarshaller(ProtoUtils.marshaller(GetModuleHealthSnapshot_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(GetModuleHealthSnapshot_Response.getDefaultInstance()))
            .build()

    suspend fun getModuleHealthSnapshot(atlasEndpoint: String): List<ModuleHealth> {
        val endpoint = atlasClient.discoverEndpoint(atlasEndpoint, CONTRACT_VITALS_MODULES_GET)
        Log.w("RobonixVitals", "atlas=$atlasEndpoint resolved endpoint=$endpoint")
        return withContext(Dispatchers.IO) {
            val channel = channelProvider.getChannel(endpoint)
            val response = ClientCalls.blockingUnaryCall(
                channel.newCall(MODULE_HEALTH_METHOD, io.grpc.CallOptions.DEFAULT),
                GetModuleHealthSnapshot_Request.getDefaultInstance(),
            )
            val snapshot = if (response.hasSnapshot()) response.snapshot else return@withContext emptyList()
            snapshot.modulesList.map { m ->
                ModuleHealth(
                    moduleKey = m.moduleKey,
                    moduleId = m.moduleId,
                    providerId = m.providerId,
                    healthCode = m.health.toInt(),
                    health = moduleHealth(m.state, m.reasonCode, m.health.toInt()),
                    state = m.state,
                    reasonCode = m.reasonCode,
                    detail = m.detail,
                    source = m.source,
                    receivedTsNs = m.receivedTsNs,
                    ttlMs = m.ttlMs.toInt(),
                )
            }
        }
    }

    /**
     * Maps the robot's raw module health code to a health word, matching the
     * web client (`vitals_transport.py::_module_health`): the numeric field is
     * a status code (0 = ok, 1 = warn, 2 = error) — NOT a 0–100 percentage —
     * and a stale/expired state or reason overrides everything to "stale".
     */
    private fun moduleHealth(state: String, reasonCode: String, code: Int): String {
        val marker = "${state} ${reasonCode}".lowercase()
        val staleWords = listOf("stale", "expired", "unavailable", "timeout", "missing")
        if (staleWords.any { marker.contains(it) }) return "stale"
        return when (code) {
            0 -> "ok"
            1 -> "warn"
            2 -> "error"
            else -> "unknown"
        }
    }
}
