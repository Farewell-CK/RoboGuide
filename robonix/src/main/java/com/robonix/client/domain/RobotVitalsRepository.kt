package com.robonix.client.domain

import com.robonix.client.AppLog
import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.GrpcChannelProvider
import com.robonix.client.data.model.RobotDescription
import com.robonix.proto.soma.GetUrdf_Request
import com.robonix.proto.soma.GetUrdf_Response
import com.robonix.proto.soma.GetYaml_Request
import com.robonix.proto.soma.GetYaml_Response
import com.robonix.proto.vitalsstream.StreamVitals_Request
import com.robonix.proto.vitalsstream.VitalsSnapshot
import io.grpc.CallOptions
import io.grpc.MethodDescriptor
import io.grpc.protobuf.ProtoUtils
import io.grpc.stub.ClientCalls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Soma robot description + Vitals hardware stream for the 3D health view.
 * Mirrors the web client's soma/get_yaml + soma/get_urdf + vitals/stream
 * contracts (see `src/robonix_client/vitals_transport.py`).
 */
@Singleton
class RobotVitalsRepository @Inject constructor(
    private val atlasClient: AtlasClient,
    private val channelProvider: GrpcChannelProvider,
) {
    companion object {
        const val CONTRACT_SOMA_GET_YAML = "robonix/system/soma/get_yaml"
        const val CONTRACT_SOMA_GET_URDF = "robonix/system/soma/get_urdf"
        const val CONTRACT_VITALS_STREAM = "robonix/system/vitals/stream"

        /** Virtual base URL the WebView intercepts to serve STL meshes from memory. */
        const val ASSET_BASE_URL = "https://robonix.local/assets/"
    }

    private val GET_YAML_METHOD: MethodDescriptor<GetYaml_Request, GetYaml_Response> =
        MethodDescriptor.newBuilder<GetYaml_Request, GetYaml_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemSomaGetYaml/GetYaml")
            .setRequestMarshaller(ProtoUtils.marshaller(GetYaml_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(GetYaml_Response.getDefaultInstance()))
            .build()

    private val GET_URDF_METHOD: MethodDescriptor<GetUrdf_Request, GetUrdf_Response> =
        MethodDescriptor.newBuilder<GetUrdf_Request, GetUrdf_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemSomaGetUrdf/GetUrdf")
            .setRequestMarshaller(ProtoUtils.marshaller(GetUrdf_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(GetUrdf_Response.getDefaultInstance()))
            .build()

    private val STREAM_VITALS_METHOD: MethodDescriptor<StreamVitals_Request, VitalsSnapshot> =
        MethodDescriptor.newBuilder<StreamVitals_Request, VitalsSnapshot>()
            .setType(MethodDescriptor.MethodType.SERVER_STREAMING)
            .setFullMethodName("robonix.contracts.RobonixSystemVitalsStream/StreamVitals")
            .setRequestMarshaller(ProtoUtils.marshaller(StreamVitals_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(VitalsSnapshot.getDefaultInstance()))
            .build()

    /** STL/DAE meshes for the current URDF, keyed by asset path (for WebView interception). */
    private val assets = ConcurrentHashMap<String, ByteArray>()

    suspend fun loadRobotDescription(atlasEndpoint: String): RobotDescription = withContext(Dispatchers.IO) {
        val yamlEndpoint = atlasClient.discoverEndpoint(atlasEndpoint, CONTRACT_SOMA_GET_YAML)
        val yamlResponse = ClientCalls.blockingUnaryCall(
            channelProvider.getChannel(yamlEndpoint).newCall(GET_YAML_METHOD, CallOptions.DEFAULT),
            GetYaml_Request.getDefaultInstance(),
        )
        val yamlText = yamlResponse.yamlText
        val robotId = yamlResponse.robotId
        AppLog.write(
            "VITALS",
            "soma yaml from $yamlEndpoint robot=$robotId len=${yamlText.length} head=${yamlText.take(160)}\n" +
                "===YAML_BEGIN===\n$yamlText\n===YAML_END===",
        )

        var urdfXml = ""
        try {
            val urdfEndpoint = atlasClient.discoverEndpoint(atlasEndpoint, CONTRACT_SOMA_GET_URDF)
            val urdfResponse = ClientCalls.blockingUnaryCall(
                channelProvider.getChannel(urdfEndpoint).newCall(GET_URDF_METHOD, CallOptions.DEFAULT),
                GetUrdf_Request.newBuilder().setRobotId(robotId).setIncludeAssets(true).build(),
            )
            urdfXml = urdfResponse.urdfXml
            assets.clear()
            urdfResponse.assetsList.forEach { asset -> assets[asset.path] = asset.data.toByteArray() }
            AppLog.write("VITALS", "urdf from $urdfEndpoint assets=${assets.size}")
        } catch (e: Exception) {
            // URDF is optional — the YAML topology still renders as a procedural placeholder.
            AppLog.write("VITALS", "GetUrdf failed; procedural fallback", e)
        }

        try {
            return@withContext RobotVitalsMapper.normalizeRobotDescription(yamlText, urdfXml, robotId, ASSET_BASE_URL)
        } catch (e: Exception) {
            // Surface the real YAML load problem: the UI sees the generic
            // "Soma YAML must contain a mapping" but the log file keeps the
            // cause + the raw YAML dump above.
            AppLog.write("VITALS", "normalizeRobotDescription failed len=${yamlText.length}", e)
            throw e
        }
    }

    fun assetForPath(path: String): ByteArray? = assets[path]

    fun streamVitalsSnapshots(atlasEndpoint: String): Flow<VitalsSnapshot> =
        flow {
            val endpoint = atlasClient.discoverEndpoint(atlasEndpoint, CONTRACT_VITALS_STREAM)
            AppLog.write("VITALS", "vitals stream endpoint=$endpoint")
            val channel = channelProvider.getChannel(endpoint)
            val iterator = ClientCalls.blockingServerStreamingCall(
                channel.newCall(STREAM_VITALS_METHOD, CallOptions.DEFAULT),
                StreamVitals_Request.getDefaultInstance(),
            )
            while (iterator.hasNext()) emit(iterator.next())
        }.flowOn(Dispatchers.IO)
}
