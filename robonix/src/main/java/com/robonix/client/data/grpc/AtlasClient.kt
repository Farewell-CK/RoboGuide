package com.robonix.client.data.grpc

import com.robonix.client.data.model.AudioProvider
import com.robonix.client.data.model.CapabilityInfo
import com.robonix.client.data.model.ContractStatus
import com.robonix.client.data.model.ProviderInfo
import com.robonix.client.data.model.SystemSnapshot
import com.robonix.client.data.model.SystemSummary
import com.robonix.proto.atlas.ConnectCapabilityRequest
import com.robonix.proto.atlas.ConnectCapabilityResponse
import com.robonix.proto.atlas.QueryRequest
import com.robonix.proto.atlas.QueryResponse
import com.robonix.proto.atlas.Transport
import io.grpc.MethodDescriptor
import io.grpc.protobuf.ProtoUtils
import io.grpc.stub.ClientCalls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AtlasClient @Inject constructor(
    private val channelProvider: GrpcChannelProvider,
) {
    // Atlas uses the standard proto package path, not contracts path
    private val QUERY_METHOD: MethodDescriptor<QueryRequest, QueryResponse> =
        MethodDescriptor.newBuilder<QueryRequest, QueryResponse>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.atlas.Atlas/Query")
            .setRequestMarshaller(ProtoUtils.marshaller(QueryRequest.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(QueryResponse.getDefaultInstance()))
            .build()

    private val CONNECT_METHOD: MethodDescriptor<ConnectCapabilityRequest, ConnectCapabilityResponse> =
        MethodDescriptor.newBuilder<ConnectCapabilityRequest, ConnectCapabilityResponse>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.atlas.Atlas/ConnectCapability")
            .setRequestMarshaller(ProtoUtils.marshaller(ConnectCapabilityRequest.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(ConnectCapabilityResponse.getDefaultInstance()))
            .build()

    suspend fun queryProviders(target: String, contractId: String = ""): List<ProviderInfo> =
        withContext(Dispatchers.IO) {
            val channel = channelProvider.getChannel(target)

            val request = QueryRequest.newBuilder().apply {
                if (contractId.isNotBlank()) this.contractId = contractId
                transport = Transport.TRANSPORT_GRPC
            }.build()

            val response = ClientCalls.blockingUnaryCall(
                channel.newCall(QUERY_METHOD, io.grpc.CallOptions.DEFAULT), request)

            response.providersList.map { provider ->
                ProviderInfo(
                    id = provider.id,
                    kind = provider.kind.name,
                    namespace = provider.namespace,
                    state = provider.state.name.removePrefix("STATE_"),
                    stateDetail = provider.stateDetail,
                    capabilities = provider.capabilitiesList.map { cap ->
                        CapabilityInfo(
                            contractId = cap.contractId,
                            transport = cap.transport.name,
                            description = cap.description,
                        )
                    },
                )
            }
        }

    suspend fun connectCapability(
        target: String, providerId: String, contractId: String,
        consumerId: String = "robonix-client/android",
    ): String = withContext(Dispatchers.IO) {
        val channel = channelProvider.getChannel(target)
        val request = ConnectCapabilityRequest.newBuilder()
            .setConsumerId(consumerId).setProviderId(providerId)
            .setContractId(contractId).setTransport(Transport.TRANSPORT_GRPC)
            .build()
        val rawEndpoint = ClientCalls.blockingUnaryCall(
            channel.newCall(CONNECT_METHOD, io.grpc.CallOptions.DEFAULT), request).endpoint
        // Rewrite loopback addresses to the actual robot host, matching the
        // web client's rewrite_remote_endpoint. When a provider advertises
        // 127.0.0.1:PORT, it means the service is on the robot — not on this
        // Android device.
        rewriteRemoteEndpoint(rawEndpoint, target)
    }

    suspend fun getSystemSnapshot(target: String): SystemSnapshot = withContext(Dispatchers.IO) {
        try {
            val providers = queryProviders(target)
            val active = providers.count { it.state == "ACTIVE" }
            val errors = providers.count { it.state == "ERROR" }
            val terminated = providers.count { it.state == "TERMINATED" }
            SystemSnapshot(
                atlasEndpoint = target, providers = providers,
                requiredContracts = buildContractStatus(providers),
                summary = SystemSummary(
                    providers = providers.size, active = active,
                    errors = errors, terminated = terminated,
                    state = when {
                        errors > 0 || terminated > 0 -> "degraded"
                        active > 0 -> "ready"
                        else -> "idle"
                    },
                ),
            )
        } catch (e: Exception) {
            SystemSnapshot(atlasEndpoint = target,
                error = e.message ?: "unknown error",
                summary = SystemSummary(state = "offline"))
        }
    }

    private fun buildContractStatus(providers: List<ProviderInfo>): List<ContractStatus> {
        val contracts = listOf(
            "Liaison submit" to "robonix/system/liaison/submit",
            "Liaison voice" to "robonix/system/liaison/voice",
            "Pilot" to "robonix/system/pilot",
            "Executor" to "robonix/system/executor/execute",
            "Mic" to "robonix/primitive/audio/mic",
            "Speaker" to "robonix/primitive/audio/speaker",
            "ASR" to "robonix/service/speech/asr",
            "TTS" to "robonix/service/speech/tts",
            "Voiceprint" to "robonix/service/voiceprint/identify",
        )
        return contracts.map { (label, cid) ->
            val matching = providers.filter { p -> p.capabilities.any { it.contractId == cid } }
            ContractStatus(label = label, contractId = cid,
                available = matching.isNotEmpty(), providers = matching.map { it.id })
        }
    }

    suspend fun discoverEndpoint(
        target: String, contractId: String, providerHint: String = "",
    ): String = withContext(Dispatchers.IO) {
        val providers = queryProviders(target, contractId)
        val provider = (if (providerHint.isNotBlank())
            providers.find { it.id == providerHint || it.namespace == providerHint }
        else providers.firstOrNull { p -> p.capabilities.any { it.contractId == contractId } })
            ?: throw RuntimeException("no provider found for $contractId")
        connectCapability(target, provider.id, contractId)
    }

    suspend fun listAudioProviders(target: String):
        Triple<List<AudioProvider>, List<AudioProvider>, List<AudioProvider>> =
        withContext(Dispatchers.IO) {
            Triple(
                queryProviders(target, "robonix/primitive/audio/mic").map { AudioProvider(it.id, it.namespace) },
                queryProviders(target, "robonix/primitive/audio/speaker").map { AudioProvider(it.id, it.namespace) },
                queryProviders(target, "robonix/primitive/audio/bridge_info").map { AudioProvider(it.id, it.namespace) },
            )
        }

    companion object {
        /**
         * When a provider advertises a loopback address (127.0.0.1, localhost, ::1),
         * replace the host portion with the actual Atlas host. This matches the web
         * client's rewrite_remote_endpoint behaviour: a provider running on the robot
         * may bind to 0.0.0.0 or 127.0.0.1, but the endpoint it returns refers to
         * itself, not to this device.
         *
         * Additionally, this is a single-robot client: every subsystem (liaison,
         * executor, vitals, audio, handsfree, ...) is co-located on the robot that
         * Atlas points at. A provider may advertise the robot's *other* address
         * (e.g. its Tailscale/mesh IP, reachable by the web client but not by a
         * phone on the robot's LAN). In that case we still route to the port the
         * provider advertised, but on the Atlas host — the one address this phone
         * is configured to reach. When the advertised host already IS the Atlas
         * host this is a no-op.
         */
        fun rewriteRemoteEndpoint(endpoint: String, atlasTarget: String): String {
            val raw = endpoint.trim()
            if (raw.isEmpty()) return raw

            val atlasHost = atlasTarget.substringBeforeLast(":")
            if (atlasHost.isBlank()) return raw

            // Split endpoint into host:port
            val lastColon = raw.lastIndexOf(':')
            if (lastColon < 0) return raw

            val endpointHost = raw.substring(0, lastColon)
            val endpointPort = raw.substring(lastColon + 1)

            val hostIsLoopback = endpointHost.isEmpty() ||
                endpointHost == "127.0.0.1" ||
                endpointHost.equals("localhost", ignoreCase = true) ||
                endpointHost == "::1" ||
                endpointHost == "0.0.0.0"

            val hostDiffers = endpointHost != atlasHost

            return if (hostIsLoopback || hostDiffers) {
                "$atlasHost:$endpointPort"
            } else {
                raw
            }
        }
    }
}
