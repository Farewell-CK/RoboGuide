package com.robonix.client.ui.vitals

import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.robonix.client.AppLog
import com.robonix.client.data.model.RobotDescription
import com.robonix.client.domain.RobotVitalsRepository
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream

/**
 * WebView-hosted three.js URDF viewer. Reuses the same rendering semantics as
 * the web vitals panel: URDF XML is injected into a bundled three.js +
 * urdf-loader script, STL/DAE meshes are served from memory via
 * [android.webkit.WebViewClient.shouldInterceptRequest], and per-component
 * health is applied as material emissive/color highlight.
 */
@Composable
fun UrdfViewer(
    description: RobotDescription?,
    highlights: Map<String, String>,
    assetProvider: (String) -> ByteArray?,
    modifier: Modifier = Modifier,
) {
    val state = remember { ViewerState() }

    val renderPayloadJson = remember(description) {
        description?.let { robotRenderJson(it, highlights) }
    }
    val highlightsJson = remember(highlights) { highlightsToJson(highlights) }

    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                state.webView = this
                setBackgroundColor(0xFF081115.toInt())
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.mediaPlaybackRequiresUserGesture = false
                var lastLayoutH = -1
                addOnLayoutChangeListener { _, l, t, r, b, _, _, _, _ ->
                    val h = b - t
                    if (h != lastLayoutH) {
                        lastLayoutH = h
                        AppLog.write("URDF", "webview layout h=${h}px w=${r - l}px density=${resources.displayMetrics.density}")
                    }
                }
                webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val url = request.url.toString()
                        if (!url.startsWith(RobotVitalsRepository.ASSET_BASE_URL)) return null
                        val path = Uri.decode(url.removePrefix(RobotVitalsRepository.ASSET_BASE_URL))
                        val bytes = assetProvider(path) ?: return null
                        val mime = when {
                            path.endsWith(".stl", ignoreCase = true) -> "model/stl"
                            path.endsWith(".dae", ignoreCase = true) -> "model/vnd.collada+xml"
                            path.endsWith(".png", ignoreCase = true) -> "image/png"
                            path.endsWith(".jpg", ignoreCase = true) || path.endsWith(".jpeg", ignoreCase = true) -> "image/jpeg"
                            else -> "application/octet-stream"
                        }
                        return WebResourceResponse(mime, "utf-8", ByteArrayInputStream(bytes))
                    }

                    override fun onPageFinished(view: WebView, url: String?) {
                        state.pageLoaded = true
                        view.runUrdfProbe()
                        state.latestRender?.let {
                            view.renderUrdf("onPageFinished", it)
                        }
                        // This WebView reports a 0 CSS-px viewport height even
                        // when laid out tall (percentage heights collapse), so
                        // %-height html/body/#stage yield nothing. Inject an
                        // explicit pixel height (physical px / density) on the
                        // document and let the viewer resize onto it.
                        val density = view.resources.displayMetrics.density
                        val cssHeightPx = (view.height / density).toInt().coerceAtLeast(1)
                        view.postDelayed({
                            val js = """(function(){
                                var out = { innerH: window.innerHeight };
                                var de = document.documentElement;
                                de.style.height = '$cssHeightPx' + 'px';
                                if (document.body) document.body.style.height = '$cssHeightPx' + 'px';
                                var s = document.getElementById('stage');
                                if (s) { s.style.position = 'absolute'; s.style.top = '0'; s.style.left = '0'; s.style.width = '100%'; s.style.height = '$cssHeightPx' + 'px'; }
                                out.htmlClient = de.clientHeight;
                                out.stageAfter = s ? (s.clientWidth + 'x' + s.clientHeight) : 'none';
                                try {
                                  if (window.RobonixRobotViewer) {
                                    window.RobonixRobotViewer.resize();
                                    var c = document.querySelector('canvas');
                                    if (c) out.canvas = c.width + 'x' + c.height;
                                  }
                                } catch (e) { out.err = String(e); }
                                return JSON.stringify(out);
                              })()"""
                            evaluateJavascript(js) { value ->
                                AppLog.write("URDF", "probe2(fix) => ${value ?: "null"}")
                            }
                        }, 400L)
                        view.postDelayed({
                            val js = """(function(){
                                try { return JSON.stringify(window.RobonixRobotViewer.debugInfo()); }
                                catch (e) { return 'err: ' + e.message; }
                              })()"""
                            evaluateJavascript(js) { value ->
                                AppLog.write("URDF", "debug => ${value ?: "null"}")
                            }
                        }, 900L)
                    }
                }
                loadDataWithBaseURL(
                    RobotVitalsRepository.ASSET_BASE_URL,
                    VIEWER_HTML,
                    "text/html",
                    "utf-8",
                    null,
                )
            }
        },
        update = { /* driven by LaunchedEffect below */ },
        modifier = modifier,
    )

    LaunchedEffect(renderPayloadJson) {
        state.latestRender = renderPayloadJson
        if (renderPayloadJson != null && state.pageLoaded) {
            state.webView?.renderUrdf("launched", renderPayloadJson)
        }
    }

    LaunchedEffect(highlightsJson) {
        if (state.pageLoaded) {
            state.webView?.evaluateJavascript(
                "try { window.RobonixRobotViewer.updateHighlights($highlightsJson); 'ok'; } catch (e) { 'err: ' + e.message; }",
            ) { value -> AppLog.write("URDF", "highlights => ${value ?: "null"}") }
        }
    }
}

/** Returns a JSON blob describing whether the viewer JS registered + WebGL works. */
private fun WebView.runUrdfProbe() {
    val probe = """(function(){
        var r = { api: typeof window.RobonixRobotViewer };
        try {
          var c = document.createElement('canvas');
          r.webgl = !!(c.getContext('webgl') || c.getContext('experimental-webgl'));
          r.webgl2 = !!c.getContext('webgl2');
        } catch (e) { r.webglErr = String(e); }
        var s = document.getElementById('stage');
        r.stageW = s ? s.clientWidth : -1;
        r.stageH = s ? s.clientHeight : -1;
        return JSON.stringify(r);
      })()"""
    evaluateJavascript(probe) { value ->
        AppLog.write("URDF", "probe => ${value ?: "null"}")
    }
}

/** Calls the viewer's async render(), capturing a synchronous failure (e.g. no WebGL). */
private fun WebView.renderUrdf(label: String, payloadJson: String) {
    evaluateJavascript(
        "try { window.RobonixRobotViewer.render($payloadJson); 'ok'; } catch (e) { 'err: ' + e.message; }",
    ) { value ->
        AppLog.write("URDF", "render($label) => ${value ?: "null"}")
    }
}

private class ViewerState {
    var webView: WebView? = null
    var pageLoaded: Boolean = false
    var latestRender: String? = null
}

private fun robotRenderJson(description: RobotDescription, highlights: Map<String, String>): String {
    val components = JSONArray()
    description.components.forEach { component ->
        components.put(
            JSONObject()
                .put("id", component.id)
                .put("urdfLink", component.urdfLink)
                .put("urdfJoint", component.urdfJoint),
        )
    }
    val highlightObj = JSONObject()
    highlights.forEach { (k, v) -> highlightObj.put(k, v) }
    return JSONObject()
        .put("urdfXml", description.urdfXml)
        .put("assetBaseUrl", description.urdfAssetBaseUrl.ifBlank { RobotVitalsRepository.ASSET_BASE_URL })
        .put("components", components)
        .put("highlights", highlightObj)
        .toString()
}

private fun highlightsToJson(highlights: Map<String, String>): String {
    val obj = JSONObject()
    highlights.forEach { (k, v) -> obj.put(k, v) }
    return obj.toString()
}

private const val VIEWER_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<style>
  html, body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:#081115; }
  #stage { position:absolute; inset:0; }
</style>
</head>
<body>
  <div id="stage"></div>
  <script src="file:///android_asset/robot_viewer.js"></script>
</body>
</html>
"""
