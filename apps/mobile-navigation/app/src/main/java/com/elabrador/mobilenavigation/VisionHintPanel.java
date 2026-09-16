package com.elabrador.mobilenavigation;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.text.InputType;
import android.widget.*;

final class VisionHintPanel implements AutoCloseable {
    private final Activity activity;
    private final SharedPreferences prefs;
    private final QwenVisionHints hints;
    private final Switch toggle;
    private final TextView primary,diagnostic;
    private VisionHintSnapshot latest=VisionHintSnapshot.EMPTY;
    private final Runnable onResult;
    VisionHintPanel(Activity activity,Runnable onResult){
        this.onResult=onResult;
        this.activity=activity;prefs=activity.getSharedPreferences("qwen_visual_hints",0);
        primary=activity.findViewById(R.id.visionHintText);
        diagnostic=activity.findViewById(R.id.visionHintDiagnosticText);
        toggle=activity.findViewById(R.id.visionHintEnabled);
        hints=new QwenVisionHints(activity,(main,detail,snapshot)->{
            if(!main.contentEquals(primary.getText()))primary.setText(main);
            if(!detail.contentEquals(diagnostic.getText()))diagnostic.setText(detail);
            latest=snapshot;onResult.run();
        });
        toggle.setChecked(prefs.getBoolean("enabled",true));
        toggle.setOnCheckedChangeListener((button,checked)->{prefs.edit().putBoolean("enabled",checked).apply();apply();});
        activity.findViewById(R.id.visionHintSettings).setOnClickListener(v->settings());apply();
    }
    private String value(String name,String fallback){return prefs.getString(name,fallback);}
    private void apply(){
        latest=VisionHintSnapshot.EMPTY;
        hints.configure(value("key",BuildConfig.QWEN_KEY),value("endpoint",BuildConfig.QWEN_ENDPOINT),
                value("model",BuildConfig.QWEN_MODEL),toggle.isChecked());
        onResult.run();
    }
    VisionHintSnapshot snapshot(){return toggle.isChecked()?latest:VisionHintSnapshot.EMPTY;}
    private void settings(){
        LinearLayout form=new LinearLayout(activity);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(30,10,30,10);
        EditText endpoint=new EditText(activity);endpoint.setHint("openAiCompatible HTTPS地址");
        endpoint.setText(value("endpoint",BuildConfig.QWEN_ENDPOINT));endpoint.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);form.addView(endpoint);
        EditText key=new EditText(activity);key.setHint("API Key（已隐藏）");
        key.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);key.setText(value("key",BuildConfig.QWEN_KEY));form.addView(key);
        Spinner model=new Spinner(activity);String[] models={"qwen3-vl-flash","qwen3-vl-plus"};
        model.setAdapter(new ArrayAdapter<>(activity,android.R.layout.simple_spinner_dropdown_item,models));
        model.setSelection("qwen3-vl-plus".equals(value("model",BuildConfig.QWEN_MODEL))?1:0);form.addView(model);
        AlertDialog dialog=new AlertDialog.Builder(activity).setTitle("大模型视觉提示设置").setView(form)
                .setNegativeButton("取消",null).setPositiveButton("保存",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try{
                String url=QwenVisionHints.normalizeEndpoint(endpoint.getText().toString());
                if(key.getText().toString().trim().isEmpty()){key.setError("请输入API Key");return;}
                prefs.edit().putString("endpoint",url).putString("key",key.getText().toString().trim())
                        .putString("model",models[model.getSelectedItemPosition()]).apply();apply();dialog.dismiss();
            }catch(IllegalArgumentException invalid){endpoint.setError("请输入有效的HTTPS兼容接口地址");}
        }));dialog.show();
    }
    void foreground(boolean value){hints.setForeground(value);}
    void offer(byte[] rgb,int width,int height,int stride,long capture){hints.offer(rgb,width,height,stride,capture);}
    @Override public void close(){hints.close();}
}
