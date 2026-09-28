package com.elabrador.mobilenavigation;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.*;
import android.util.Base64;
import java.io.*;
import java.net.URI;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.json.*;
import okhttp3.*;

/** Independent RGB -> cloud -> text pipeline. No access to navigation state. */
final class QwenVisionHints implements AutoCloseable {
    interface Listener {void show(String primary,String diagnostic,VisionHintSnapshot snapshot);}
    private static final long SAMPLE_MS=500,REQUEST_MS=2500;
    private final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor(r->{
        Thread t=new Thread(r,"qwen-vision-hints");t.setPriority(Thread.MIN_PRIORITY);return t;});
    private final OkHttpClient http=new OkHttpClient.Builder().connectTimeout(5,TimeUnit.SECONDS)
            .readTimeout(12,TimeUnit.SECONDS).callTimeout(15,TimeUnit.SECONDS)
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false).build();
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final AtomicReference<Raw> latest=new AtomicReference<>();
    private final AtomicLong generation=new AtomicLong();
    private final ArrayDeque<Frame> frames=new ArrayDeque<>();
    private final VisionHintState state=new VisionHintState();
    private final Listener listener;
    private volatile boolean enabled,foreground,closed;
    private volatile String key,endpoint,model;
    private volatile long lastInput=-1;
    private long lastOffer=-1,nextRequest,lastSent=-1;
    private volatile Call activeCall;
    private String lastPrimary="",lastDiagnostic="";
    private VisionHintSnapshot lastSnapshot;
    QwenVisionHints(Context context,Listener listener){
        this.listener=listener;
        worker.scheduleWithFixedDelay(()->{try{tick();}catch(Exception ignored){fail("视觉提示处理失败，等待重试");}},0,200,TimeUnit.MILLISECONDS);
    }
    static String normalizeEndpoint(String value){
        String text=value.trim().replaceAll("/+$","");
        if(!text.endsWith("/chat/completions"))text+="/chat/completions";
        URI uri=URI.create(text);
        if(!"https".equalsIgnoreCase(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null
                ||uri.getQuery()!=null||uri.getFragment()!=null)throw new IllegalArgumentException("请填写HTTPS兼容接口地址");
        return text;
    }
    void configure(String key,String endpoint,String model,boolean enabled){
        this.key=key.trim();this.endpoint=endpoint.trim();this.model=model.trim();this.enabled=enabled;
        reset(enabled?"等待彩色画面":"已关闭",true);
    }
    void setForeground(boolean foreground){this.foreground=foreground;reset(foreground?"等待彩色画面":"后台暂停上传",false);}
    private void reset(String message,boolean clearPrimary){
        long g=generation.incrementAndGet();latest.set(null);lastInput=-1;
        Call call=activeCall;if(call!=null)call.cancel();activeCall=null;
        execute(()->{if(g!=generation.get())return;frames.clear();lastSent=-1;nextRequest=0;
            lastPrimary="";lastDiagnostic="";
            if(clearPrimary)state.reset(message);else state.status(message);
            publish();});
    }
    /** Bounded owned copy at 2Hz; no encoding/network on the RealSense thread. */
    synchronized void offer(byte[] rgb,int width,int height,int stride,long capture){
        if(closed||!enabled||!foreground||key==null||key.isEmpty()||endpoint==null||endpoint.isEmpty())return;
        long now=SystemClock.elapsedRealtime();
        if(now-capture<0||now-capture>=1000||width<=0||height<=0||width>1920||height>1080
                ||stride<width*3||(long)stride*height>rgb.length)return;
        if(lastOffer>=0&&now-lastOffer<SAMPLE_MS)return;
        lastOffer=now;lastInput=now;
        latest.set(new Raw(Arrays.copyOf(rgb,stride*height),width,height,stride,capture,generation.get()));
    }
    private void tick()throws Exception {
        if(closed)return;
        long now=SystemClock.elapsedRealtime();
        if(!enabled){state.status("已关闭");publish();return;}
        if(!foreground){state.status("后台暂停上传");publish();return;}
        if(key==null||key.isEmpty()||endpoint==null||endpoint.isEmpty()){
            state.status("未配置接口，请打开设置");publish();return;
        }
        Raw raw=latest.getAndSet(null);
        if(raw!=null&&raw.generation==generation.get()&&now-raw.capture<1500){
            int step=Math.max(1,(raw.width+639)/640);
            int[] pixels=RgbPreviewPixels.convert(raw.rgb,raw.width,raw.height,raw.stride,step);
            Bitmap bitmap=Bitmap.createBitmap(pixels,(raw.width+step-1)/step,(raw.height+step-1)/step,Bitmap.Config.ARGB_8888);
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            try{bitmap.compress(Bitmap.CompressFormat.JPEG,70,out);}finally{bitmap.recycle();}
            frames.addLast(new Frame(out.toByteArray(),raw.capture));while(frames.size()>4)frames.removeFirst();
        }
        while(!frames.isEmpty()&&now-frames.getFirst().capture>2500)frames.removeFirst();
        if(lastInput<0||now-lastInput>2000){
            Call call=activeCall;if(call!=null){generation.incrementAndGet();call.cancel();activeCall=null;}
            frames.clear();state.status("等待新鲜彩色画面");publish();return;
        }
        if(activeCall==null&&now>=nextRequest&&frames.size()>=4&&frames.getLast().capture>lastSent){
            send(new ArrayList<>(frames),now);
        }
        publish();
    }
    private void send(List<Frame> input,long now)throws Exception {
        final long g=generation.get(),capture=input.get(input.size()-1).capture;
        JSONArray content=new JSONArray();
        content.put(new JSONObject().put("type","text").put("text",VisionHintProtocol.PROMPT));
        for(Frame frame:input){
            content.put(new JSONObject().put("type","text").put("text","相对最新帧时间："+(frame.capture-capture)+"ms"));
            content.put(new JSONObject().put("type","image_url").put("image_url",new JSONObject()
                    .put("url","data:image/jpeg;base64,"+Base64.encodeToString(frame.jpeg,Base64.NO_WRAP))));
        }
        JSONObject request=new JSONObject().put("model",model).put("temperature",0).put("max_tokens",500)
                .put("stream",false).put("messages",new JSONArray().put(new JSONObject().put("role","user").put("content",content)));
        Request httpRequest=new Request.Builder().url(normalizeEndpoint(endpoint)).header("Authorization","Bearer "+key)
                .post(RequestBody.create(request.toString(),MediaType.get("application/json; charset=utf-8"))).build();
        Call call=http.newCall(httpRequest);activeCall=call;nextRequest=now+REQUEST_MS;lastSent=capture;
        call.enqueue(new Callback(){
            public void onFailure(Call c,IOException error){execute(()->{
                if(g!=generation.get()||closed)return;activeCall=null;
                fail(error instanceof java.io.InterruptedIOException?"服务响应超时，稍后重试":"网络不可用，视觉提示暂停");
                nextRequest=SystemClock.elapsedRealtime()+3000;
            });}
            public void onResponse(Call c,Response response){
                try(Response owned=response){
                    if(!owned.isSuccessful()){
                        int status=owned.code();execute(()->{if(g!=generation.get()||closed)return;activeCall=null;
                            fail(status==401||status==403?"鉴权或模型权限失败，请检查设置":status==429?"额度或调用频率受限":"视觉服务暂不可用（HTTP "+status+"）");
                            nextRequest=SystemClock.elapsedRealtime()+((status==401||status==403)?60000:10000);});return;
                    }
                    if(owned.body()==null)throw new IOException("empty body");
                    String body=readBounded(owned.body().byteStream());
                    VisionHintProtocol.Result result=VisionHintProtocol.parse(body);
                    execute(()->{if(g!=generation.get()||closed)return;activeCall=null;
                        long completed=SystemClock.elapsedRealtime();state.accept(result,capture,completed,completed-now);publish();});
                }catch(Exception error){execute(()->{if(g!=generation.get()||closed)return;activeCall=null;
                    fail("返回内容无法可靠解析，等待重试");nextRequest=SystemClock.elapsedRealtime()+3000;});}
            }
        });
    }
    private static String readBounded(InputStream stream)throws IOException {
        ByteArrayOutputStream output=new ByteArrayOutputStream();byte[] buffer=new byte[4096];int n;
        while((n=stream.read(buffer))!=-1){if(output.size()+n>65536)throw new IOException("response too large");output.write(buffer,0,n);}
        return output.toString("UTF-8");
    }
    private void fail(String message){state.status(message);publish();}
    private void publish(){
        long now=SystemClock.elapsedRealtime();
        String primary=state.primaryText();
        String diagnostic=state.diagnosticText(now,activeCall!=null);
        VisionHintSnapshot snapshot=state.snapshot();
        if(primary.equals(lastPrimary)&&diagnostic.equals(lastDiagnostic)&&snapshot==lastSnapshot)return;
        lastPrimary=primary;lastDiagnostic=diagnostic;
        lastSnapshot=snapshot;
        String displayPrimary=primary,displayDiagnostic=diagnostic;long g=generation.get();
        ui.post(()->{if(!closed&&g==generation.get())listener.show(displayPrimary,displayDiagnostic,snapshot);});
    }
    private void execute(Runnable task){try{worker.execute(task);}catch(RejectedExecutionException ignored){}}
    @Override public void close(){closed=true;generation.incrementAndGet();Call call=activeCall;if(call!=null)call.cancel();
        latest.set(null);worker.shutdownNow();http.dispatcher().executorService().shutdown();http.connectionPool().evictAll();ui.removeCallbacksAndMessages(null);}
    private static final class Raw {final byte[] rgb;final int width,height,stride;final long capture,generation;
        Raw(byte[] rgb,int width,int height,int stride,long capture,long generation){this.rgb=rgb;this.width=width;this.height=height;this.stride=stride;this.capture=capture;this.generation=generation;}}
    private static final class Frame {final byte[] jpeg;final long capture;Frame(byte[] jpeg,long capture){this.jpeg=jpeg;this.capture=capture;}}
}
