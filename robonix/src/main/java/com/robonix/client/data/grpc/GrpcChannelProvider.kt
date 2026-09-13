package com.robonix.client.data.grpc

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import io.grpc.ManagedChannel
import io.grpc.okhttp.OkHttpChannelBuilder
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Hands out one long-lived gRPC channel per distinct endpoint (host:port).
 *
 * The previous implementation kept a *single* shared channel and shut it down
 * whenever a different endpoint was requested. That let unrelated subsystems
 * tear down each other's in-flight calls — most visibly the long-lived
 * vitals/voice/handsfree server-streams, which died every time a poll or
 * discovery dialed another endpoint (the web client never hit this because it
 * gives every RPC its own channel). A channel that is currently serving a call
 * must never be torn down by another call.
 *
 * Now each endpoint gets its own channel that lives until [shutdown] is called
 * (robot host change / process teardown), so concurrent calls and streams to
 * different endpoints coexist.
 */
@Singleton
class GrpcChannelProvider @Inject constructor(
    private val context: Context,
) {
    private val channels = ConcurrentHashMap<String, ManagedChannel>()

    fun getChannel(target: String): ManagedChannel {
        val key = target.trim()
        require(key.isNotBlank()) { "gRPC target must not be empty" }
        // compute() is atomic per key: only one channel is built per endpoint
        // even when several callers race. A channel that was shut down by
        // [shutdown] is transparently rebuilt on next use.
        return channels.compute(key) { _, existing ->
            existing?.takeUnless { it.isShutdown } ?: buildChannel(key)
        }!!
    }

    private fun buildChannel(target: String): ManagedChannel =
        OkHttpChannelBuilder.forTarget(target)
            .usePlaintext()
            .keepAliveTime(30, TimeUnit.SECONDS)
            .keepAliveTimeout(10, TimeUnit.SECONDS)
            .keepAliveWithoutCalls(true)
            .build()

    /**
     * Shut down every cached channel. Called when the robot host changes so
     * channels to the old robot (and their open streams) are released; later
     * calls rebuild lazily via [getChannel].
     */
    fun shutdown() {
        val closing = channels.values.toList()
        channels.clear()
        closing.forEach { it.shutdown() }
    }

    fun networkState(): Flow<Boolean> = callbackFlow {
        val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                trySend(true)
            }

            override fun onLost(network: Network) {
                trySend(false)
            }

            override fun onCapabilitiesChanged(
                network: Network,
                capabilities: NetworkCapabilities,
            ) {
                val hasInternet = capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                trySend(hasInternet)
            }
        }

        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()

        connectivityManager.registerNetworkCallback(request, callback)

        // Initial state
        val activeNetwork = connectivityManager.activeNetwork
        val caps = activeNetwork?.let { connectivityManager.getNetworkCapabilities(it) }
        trySend(caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true)

        awaitClose { connectivityManager.unregisterNetworkCallback(callback) }
    }.distinctUntilChanged()
}
