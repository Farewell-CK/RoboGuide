import com.sun.jdi.*;
import com.sun.jdi.connect.*;
import com.sun.jdi.event.*;
import com.sun.jdi.request.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;

/** Read-only debugger capture of already deprojected real depth and its original pose. */
public final class CaptureLiveGeometry {
    static Value field(ObjectReference o,String name) {
        if(o==null)return null;Field f=o.referenceType().fieldByName(name);
        if(f==null)throw new IllegalArgumentException("Missing field "+name+" on "+o.referenceType().name());
        return o.getValue(f);
    }
    static double number(Value v) { return ((PrimitiveValue)v).doubleValue(); }
    static void floats(DataOutputStream out,Value v) throws Exception {
        List<Value> values=((ArrayReference)v).getValues();out.writeInt(values.size());
        for(Value value:values)out.writeFloat(((PrimitiveValue)value).floatValue());
    }
    static void ints(DataOutputStream out,Value v) throws Exception {
        List<Value> values=((ArrayReference)v).getValues();out.writeInt(values.size());
        for(Value value:values)out.writeInt(((PrimitiveValue)value).intValue());
    }
    static void doubles(DataOutputStream out,Value v) throws Exception {
        List<Value> values=((ArrayReference)v).getValues();out.writeInt(values.size());
        for(Value value:values)out.writeDouble(number(value));
    }
    static void evidence(DataOutputStream out,ObjectReference object) throws Exception {
        out.writeBoolean(object!=null);if(object==null)return;
        out.writeDouble(number(field(object,"cameraSeconds")));
        out.writeLong(((LongValue)field(object,"generation")).longValue());
        out.writeLong(((LongValue)field(object,"captureElapsedMillis")).longValue());
    }
    static void pose(DataOutputStream out,ObjectReference object) throws Exception {
        out.writeBoolean(object!=null);if(object==null)return;
        for(String key:new String[]{"x","y","z","qx","qy","qz","qw"})out.writeDouble(number(field(object,key)));
        out.writeBoolean(((BooleanValue)field(object,"initialized")).booleanValue());
        out.writeDouble(number(field(object,"timestamp")));
        doubles(out,field(object,"imuFromCameraRotation"));doubles(out,field(object,"imuFromCameraTranslation"));
    }
    public static void main(String[] args) throws Exception {
        Path directory=Paths.get(args[0]);Files.createDirectories(directory);
        AttachingConnector connector=Bootstrap.virtualMachineManager().attachingConnectors().stream()
            .filter(c->c.name().equals("com.sun.jdi.SocketAttach")).findFirst().orElseThrow();
        Map<String,Connector.Argument> arguments=connector.defaultArguments();
        arguments.get("hostname").setValue("127.0.0.1");arguments.get("port").setValue("8701");
        arguments.get("timeout").setValue("10000");VirtualMachine vm=connector.attach(arguments);
        try {
            ReferenceType type=vm.classesByName("com.elabrador.mobilenavigation.LiveDepthMap").get(0);
            Method method=type.methodsByName("build").get(0);
            BreakpointRequest breakpoint=vm.eventRequestManager().createBreakpointRequest(method.location());
            breakpoint.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);breakpoint.enable();
            long deadline=System.currentTimeMillis()+45000;int captured=0;
            while(captured<3 && System.currentTimeMillis()<deadline) {
                EventSet set=vm.eventQueue().remove(2000);if(set==null)continue;
                try {
                    for(Event event:set) if(event instanceof BreakpointEvent hit) {
                        List<Value> input=hit.thread().frame(0).getArgumentValues();
                        ObjectReference cloud=(ObjectReference)input.get(0),prior=(ObjectReference)input.get(3);
                        Path file=directory.resolve("device-depth-"+captured+".bin");
                        try(DataOutputStream out=new DataOutputStream(new BufferedOutputStream(Files.newOutputStream(file)))) {
                            out.writeInt(0x44505431);out.writeLong(System.currentTimeMillis());
                            floats(out,field(cloud,"xyz"));ints(out,field(cloud,"semanticRgb"));floats(out,field(cloud,"confidence"));
                            pose(out,(ObjectReference)input.get(1));evidence(out,(ObjectReference)input.get(2));
                            out.writeInt(((IntegerValue)input.get(4)).intValue());out.writeLong(((LongValue)input.get(5)).longValue());
                            boolean hasPrior=prior!=null&&field(prior,"localCostGrid")!=null;out.writeBoolean(hasPrior);
                            if(hasPrior){ints(out,field(prior,"localCostGrid"));pose(out,(ObjectReference)field(prior,"mapPose"));evidence(out,(ObjectReference)field(prior,"evidence"));}
                        }
                        System.out.println("CAPTURED "+file+" bytes="+Files.size(file));captured++;
                    }
                } finally { set.resume(); }
            }
            breakpoint.disable();if(captured==0)throw new IllegalStateException("No real depth frame captured");
        } finally { vm.dispose(); }
    }
}
