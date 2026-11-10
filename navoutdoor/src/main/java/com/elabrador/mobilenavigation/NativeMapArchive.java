package com.elabrador.mobilenavigation;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.FileInputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Pinned-library full tree adapter. The .sem sidecar preserves state omitted by native .ot. */
final class NativeMapArchive {
    static final String EXPECTED_LIBRARY_SHA256="cf6006b3dd366788a88febe994302be77d431c62d501feabeaa58a349261ed61";
    private static volatile boolean checked,verified;
    private static volatile String verificationError="NativeMapArchive.initialize(Context) was not called";
    static {System.loadLibrary("swinl_plan");System.loadLibrary("elabrador_native");}
    private NativeMapArchive() {}

    /** Verify actual packaged/extracted bytes before any private ABI field can be read. */
    static synchronized void initialize(Context context) {
        if(checked)return;
        checked=true;
        try {
            ApplicationInfo application=context.getApplicationContext().getApplicationInfo();
            File extracted=new File(application.nativeLibraryDir,"libelabrador_native.so");
            if(extracted.isFile()) {
                try(InputStream in=new FileInputStream(extracted)){verify(in);}
            } else {
                List<String> archives=new ArrayList<>();archives.add(application.sourceDir);
                if(application.splitSourceDirs!=null)java.util.Collections.addAll(archives,application.splitSourceDirs);
                boolean found=false;
                for(String archive:archives) {
                    try(ZipFile zip=new ZipFile(archive)) {
                        ZipEntry entry=zip.getEntry("lib/arm64-v8a/libelabrador_native.so");
                        if(entry==null)continue;
                        try(InputStream in=zip.getInputStream(entry)){verify(in);}
                        found=true;break;
                    }
                }
                if(!found)throw new IOException("Bundled arm64 OctoMap library not found");
            }
            verified=true;verificationError="";
        }catch(Exception error){verificationError=error.toString();}
    }
    private static void verify(InputStream in)throws Exception {
        MessageDigest digest=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[65536];int n;
        while((n=in.read(buffer))!=-1)digest.update(buffer,0,n);
        StringBuilder actual=new StringBuilder();for(byte value:digest.digest())actual.append(String.format(java.util.Locale.US,"%02x",value&255));
        if(!EXPECTED_LIBRARY_SHA256.equals(actual.toString()))throw new IOException("Unsupported OctoMap binary; full-state adapter disabled");
    }
    static int nativeSaveVerified(long tree,String destination)throws IOException {
        if(!verified)throw new IOException("OctoMap ABI fingerprint verification failed: "+verificationError);
        return nativeSaveVerifiedPinned(tree,destination);
    }
    private static native int nativeSaveVerifiedPinned(long tree,String destination)throws IOException;
}
