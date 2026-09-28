package com.robonix.client.di

import android.content.Context
import com.robonix.client.data.audio.AudioBridge
import com.robonix.client.data.audio.AudioPlayer
import com.robonix.client.data.audio.AudioRecorder
import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.ExecutorClient
import com.robonix.client.data.grpc.GrpcChannelProvider
import com.robonix.client.data.grpc.LiaisonClient
import com.robonix.client.data.local.SettingsStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {
    @Provides
    @Singleton
    fun provideGrpcChannelProvider(@ApplicationContext context: Context): GrpcChannelProvider =
        GrpcChannelProvider(context)

    @Provides
    @Singleton
    fun provideAtlasClient(channelProvider: GrpcChannelProvider): AtlasClient =
        AtlasClient(channelProvider)

    @Provides
    @Singleton
    fun provideLiaisonClient(channelProvider: GrpcChannelProvider): LiaisonClient =
        LiaisonClient(channelProvider)

    @Provides
    @Singleton
    fun provideExecutorClient(channelProvider: GrpcChannelProvider): ExecutorClient =
        ExecutorClient(channelProvider)

    @Provides
    @Singleton
    fun provideSettingsStore(@ApplicationContext context: Context): SettingsStore =
        SettingsStore(context)

    @Provides
    @Singleton
    fun provideAudioRecorder(@ApplicationContext context: Context): AudioRecorder =
        AudioRecorder(context)

    @Provides
    @Singleton
    fun provideAudioPlayer(): AudioPlayer = AudioPlayer()

    @Provides
    @Singleton
    fun provideAudioBridge(): AudioBridge = AudioBridge()
}
