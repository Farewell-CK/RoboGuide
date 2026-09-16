package com.elabrador.mobilenavigation;

import android.content.Context;
import android.util.Log;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.atomic.AtomicLong;

/** Bounded asynchronous local diagnostics, two files of at most approximately 10 MiB each. */
final class NavigationAudit {
    private static final ArrayBlockingQueue<String> queue=new ArrayBlockingQueue<>(512);
    private static final AtomicLong dropped=new AtomicLong();
    private static boolean started;
    static synchronized void start(Context context){
        if(started)return;started=true;
        File dir=new File(context.getFilesDir(),"outdoor-audit");
        Thread writer=new Thread(()->{
            FileOutputStream out=null;
            try {
                if(!dir.isDirectory()&&!dir.mkdirs())return;
                File current=new File(dir,"current.log"),previous=new File(dir,"previous.log");
                long size=current.length();
                out=new FileOutputStream(current,true);
                while(true){
                    String message=queue.take();
                    Log.i("NavigationAudit",message);
                    long lost=dropped.getAndSet(0);
                    byte[] bytes=((lost>0?"AUDIT_DROPPED "+lost+"\n":"")+message+"\n").getBytes(StandardCharsets.UTF_8);
                    if(size+bytes.length>10*1024*1024){
                        out.close();
                        if(previous.exists()&&!previous.delete())throw new java.io.IOException("Cannot rotate audit");
                        if(!current.renameTo(previous))throw new java.io.IOException("Cannot rotate current audit");
                        out=new FileOutputStream(current);size=0;
                    }
                    out.write(bytes);size+=bytes.length;
                }
            }catch(Exception error){Log.e("NavigationAudit","Local audit unavailable",error);}
            finally{if(out!=null)try{out.close();}catch(Exception ignored){}}
        },"outdoor-audit");
        writer.setDaemon(true);writer.start();
        log("SESSION_START version="+BuildConfig.VERSION_NAME);
    }
    static void log(String message){
        if(!queue.offer(System.currentTimeMillis()+" "+message))dropped.incrementAndGet();
    }
}
