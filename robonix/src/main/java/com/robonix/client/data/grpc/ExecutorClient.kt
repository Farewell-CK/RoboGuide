package com.robonix.client.data.grpc

import com.robonix.client.data.model.ExecutorOp
import com.robonix.client.data.model.ExecutorPlan
import com.robonix.proto.executor.ListActivePlans_Request
import com.robonix.proto.executor.ListActivePlans_Response
import io.grpc.MethodDescriptor
import io.grpc.protobuf.ProtoUtils
import io.grpc.stub.ClientCalls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ExecutorClient @Inject constructor(
    private val channelProvider: GrpcChannelProvider,
) {
    // Executor uses the contracts path, matching the robonix backend
    private val LIST_PLANS_METHOD: MethodDescriptor<ListActivePlans_Request, ListActivePlans_Response> =
        MethodDescriptor.newBuilder<ListActivePlans_Request, ListActivePlans_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemExecutorListActivePlans/ListActivePlans")
            .setRequestMarshaller(ProtoUtils.marshaller(ListActivePlans_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(ListActivePlans_Response.getDefaultInstance()))
            .build()

    suspend fun listActivePlans(target: String): List<ExecutorPlan> = withContext(Dispatchers.IO) {
        val channel = channelProvider.getChannel(target)
        val request = ListActivePlans_Request.newBuilder().build()
        val response = ClientCalls.blockingUnaryCall(
            channel.newCall(LIST_PLANS_METHOD, io.grpc.CallOptions.DEFAULT), request)

        if (!response.success)
            throw RuntimeException(response.error.ifBlank { "Executor active-plan query failed" })

        val root = JSONObject(response.plansJson)
        val plansArray = root.optJSONArray("plans") ?: JSONArray()
        (0 until plansArray.length()).map { i ->
            val plan = plansArray.getJSONObject(i)
            ExecutorPlan(
                planId = plan.optString("plan_id", ""),
                description = plan.optString("description", ""),
                opCount = plan.optInt("op_count", 0),
                cancelled = plan.optBoolean("cancelled", false),
                ops = plan.optJSONArray("ops")?.let { ops ->
                    (0 until ops.length()).map { j ->
                        val op = ops.getJSONObject(j)
                        ExecutorOp(
                            opId = op.optString("op_id", ""),
                            description = op.optString("description", ""),
                            kind = op.optString("kind", "do"),
                            state = op.optString("state", "pending"),
                            providerId = op.optString("provider_id", ""),
                            contractId = op.optString("contract_id", ""),
                        )
                    }
                } ?: emptyList(),
            )
        }
    }
}
