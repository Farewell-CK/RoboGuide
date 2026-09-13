package com.seaway.guideassistant.module

import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.GrpcChannelProvider
import com.robonix.client.data.grpc.LiaisonClient
import com.robonix.client.domain.ChatRepository
import com.seaway.guideassistant.MyApplication
import org.koin.dsl.module

/**
 * 手动构造 `:robonix` 模块里原有的 gRPC 连接类，不经过 Hilt（该模块未启用 Hilt 处理器）。
 * 这几个类本身就是普通的 `@Inject constructor`，忽略掉 Hilt 注解直接 new 即可。
 */
val robotModule = module {
    single { GrpcChannelProvider(MyApplication.instance) }
    single { AtlasClient(get()) }
    single { LiaisonClient(get()) }
    single { ChatRepository(get(), get()) }
}
