package com.seaway.guideassistant.naviindoor.runtime

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.exifinterface.media.ExifInterface
import androidx.lifecycle.LifecycleOwner
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executor

class NaviCamera(private val context: Context, private val owner: LifecycleOwner, private val worker: Executor) {
    private var provider: ProcessCameraProvider? = null
    private var preview: Preview? = null
    private var capture: ImageCapture? = null
    private var generation = 0
    fun bind(view: PreviewView, onReady: () -> Unit, onError: (Throwable) -> Unit) {
        close()
        val token = generation
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            if (token != generation) return@addListener
            try {
                val p = future.get()
                val live = Preview.Builder().setTargetResolution(android.util.Size(720, 1280)).build()
                live.setSurfaceProvider(view.surfaceProvider)
                val shot = ImageCapture.Builder().setTargetResolution(android.util.Size(720, 1280))
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY).setJpegQuality(85).build()
                p.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, live, shot)
                provider = p; preview = live; capture = shot
                onReady()
            } catch (e: Exception) { onError(e) }
        }, ContextCompat.getMainExecutor(context))
    }
    fun take(onResult: (ByteArray) -> Unit, onError: (Throwable) -> Unit) {
        val shot = capture ?: return onError(IllegalStateException("相机正在准备，请稍后重试"))
        val token = generation
        val output = File.createTempFile("navi-", ".jpg", context.cacheDir)
        shot.takePicture(ImageCapture.OutputFileOptions.Builder(output).build(), worker, object: ImageCapture.OnImageSavedCallback {
            override fun onImageSaved(result: ImageCapture.OutputFileResults) {
                try {
                    val decoded = BitmapFactory.decodeFile(output.path) ?: error("无法读取相机画面")
                    val exif = ExifInterface(output)
                    val matrix = Matrix().apply {
                        if (exif.isFlipped) postScale(-1f, 1f)
                        postRotate(exif.rotationDegrees.toFloat())
                    }
                    val rotated = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
                    val bytes = ByteArrayOutputStream().use { stream -> rotated.compress(Bitmap.CompressFormat.JPEG, 85, stream); stream.toByteArray() }
                    if (rotated !== decoded) rotated.recycle()
                    decoded.recycle()
                    ContextCompat.getMainExecutor(context).execute { if (token == generation) onResult(bytes) }
                } catch (e: Exception) { ContextCompat.getMainExecutor(context).execute { if (token == generation) onError(e) } }
                finally { output.delete() }
            }
            override fun onError(exception: ImageCaptureException) {
                output.delete()
                ContextCompat.getMainExecutor(context).execute { if (token == generation) onError(exception) }
            }
        })
    }
    fun close() {
        generation++
        val owned = listOfNotNull(preview, capture)
        if (owned.isNotEmpty()) provider?.unbind(*owned.toTypedArray())
        preview = null; capture = null; provider = null
    }
}
