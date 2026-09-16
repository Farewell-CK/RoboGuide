package com.elabrador.mobilenavigation;

import android.app.Activity;
import android.content.SharedPreferences;
import android.view.View;
import android.widget.Switch;

/** Display preferences only; hiding diagnostics never stops navigation or cloud inference. */
final class DemoPanels {
    static void bind(Activity activity){
        SharedPreferences prefs=activity.getSharedPreferences("demo_panels",0);
        bind(activity,prefs,"perception",R.id.perceptionDebugToggle,R.id.perceptionDebugContent);
        bind(activity,prefs,"location",R.id.locationDebugToggle,R.id.locationDebugContent);
        bind(activity,prefs,"vision_details",R.id.visionDetailsToggle,R.id.visionDetailsContent);
        bind(activity,prefs,"vision_debug",R.id.visionDebugToggle,R.id.visionDebugContent);
        bind(activity,prefs,"settings",R.id.serviceSettingsToggle,R.id.serviceSettingsContent);
    }
    private static void bind(Activity activity,SharedPreferences prefs,String key,int toggleId,int contentId){
        Switch toggle=activity.findViewById(toggleId);
        View content=activity.findViewById(contentId);
        boolean shown=prefs.getBoolean(key,false);
        toggle.setChecked(shown);content.setVisibility(shown?View.VISIBLE:View.GONE);
        toggle.setOnCheckedChangeListener((button,checked)->{
            content.setVisibility(checked?View.VISIBLE:View.GONE);
            prefs.edit().putBoolean(key,checked).apply();
        });
    }
}
