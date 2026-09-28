package com.robonix.client

import android.util.Log
import java.io.File
import java.io.FileWriter
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Writes log entries to a file on device so we can read them via adb
 * even when the vendor (Vivo, Oppo, Xiaomi) filters logcat output
 * for third-party apps.
 */
object AppLog {
    private var writer: PrintWriter? = null
    private val format = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US)
    private val lock = Any()

    fun init(logDir: File) {
        synchronized(lock) {
            writer?.close()
            logDir.mkdirs()
            val f = File(logDir, "robonix.log")
            // Rotate: keep last 256 KB
            if (f.length() > 256 * 1024) {
                f.delete()
            }
            writer = PrintWriter(FileWriter(f, true), true)
            Log.w("RobonixApp", "AppLog file=${f.absolutePath}")
            write("SYSTEM", "AppLog initialized")
        }
    }

    fun write(tag: String, msg: String, tr: Throwable? = null) {
        val ts = format.format(Date())
        val line = buildString {
            append(ts)
            append(" [").append(tag).append("] ")
            append(msg)
            if (tr != null) {
                append("\n")
                val sw = StringWriter()
                tr.printStackTrace(PrintWriter(sw))
                append(sw.toString())
            }
        }
        // Also write to logcat (might be filtered, but doesn't hurt)
        Log.w("Robonix", line)
        // Write to file
        synchronized(lock) {
            try {
                writer?.println(line)
            } catch (_: Exception) {}
        }
    }

    fun close() {
        synchronized(lock) {
            try { writer?.close() } catch (_: Exception) {}
            writer = null
        }
    }
}
