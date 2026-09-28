package com.elabrador.mobilenavigation;

import java.util.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class GuidanceTextComposerTest {
    private VisionHintSnapshot scene(long capture,String... pairs){
        Map<String,Set<String>> objects=new LinkedHashMap<>();
        for(int i=0;i<pairs.length;i+=2)
            objects.computeIfAbsent(pairs[i],k->new LinkedHashSet<>()).add(pairs[i+1]);
        return new VisionHintSnapshot(capture,objects);
    }
    @Test public void concreteObjectsFollowDirectionAndEmptySectorsAreOmitted(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("左转30度，左侧有行人，右前方有电动车",c.update("左转 30°",
                scene(0,"left","行人","front_right","电动车"),0));
    }
    @Test public void identicalTypesAcrossSectorsAreCombinedWithoutChangingMeaning(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("直走，左侧、左前方有围栏，正前方有行人",
                c.update("直走",scene(0,"left","围栏","front_left","围栏","front","行人"),0));
    }
    @Test public void generalTypesAndModelCommandsNeverBecomeSpokenObjects(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("直走",c.update("直走",scene(0,"left","其他实体","front","立即右转","right","无"),0));
    }
    @Test public void noNavigationMeansNoSpeechEvenWhenObjectsExist(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("",c.update("",scene(0,"front","汽车"),0));
        assertEquals("直走，正前方有汽车",c.update("直走",scene(100,"front","汽车"),100));
        assertEquals("",c.update("",scene(100,"front","汽车"),200));
    }
    @Test public void reorderedResultsAndAliasesDoNotChangeText(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        String first=c.update("直走",scene(0,"right","人","left","建筑"),0);
        assertEquals(first,c.update("直走",scene(3100,"left","建筑物","right","行人"),3100));
    }
    @Test public void objectOnlyChangesWaitThreeSecondsButStopAndStraightAreImmediate(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("左转30度",c.update("左转 30°",scene(0),0));
        VisionHintSnapshot box=scene(500,"left","箱子");
        assertEquals("左转30度",c.update("左转 30°",box,500));
        assertEquals("左转30度，左侧有箱子",c.update("左转 30°",box,3000));
        assertEquals("停止，左侧有箱子",c.update("停止",box,3001));
        VisionHintSnapshot person=scene(3100,"left","箱子","front","行人");
        assertEquals("停止，左侧有箱子",c.update("停止",person,3100));
        assertEquals("直走，左侧有箱子，正前方有行人",c.update("直走",person,4601));
    }
    @Test public void disappearanceRequiresTwoNewObservationsNotTwoRedraws(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("直走，左侧有箱子",c.update("直走",scene(0,"left","箱子"),0));
        VisionHintSnapshot absent=scene(1000);
        assertEquals("直走，左侧有箱子",c.update("直走",absent,3000));
        assertEquals("直走，左侧有箱子",c.update("直走",absent,3100));
        assertEquals("直走",c.update("直走",scene(3200),3200));
    }
    @Test public void expiredCloudDataCannotBeAppendedToANewDirection(){
        GuidanceTextComposer c=new GuidanceTextComposer();
        VisionHintSnapshot old=scene(0,"front","汽车");
        assertEquals("直走，正前方有汽车",c.update("直走",old,0));
        assertEquals("直走，正前方有汽车",c.update("直走",old,6000));
        assertEquals("停止",c.update("停止",old,6001));
    }
    @Test public void stopHoldCannotBeInterruptedByCloudChanges(){
        GuidanceStabilizer g=new GuidanceStabilizer();
        GuidanceTextComposer c=new GuidanceTextComposer();
        assertEquals("停止",c.update(g.update("停止",true,0),scene(0),0));
        assertEquals("停止",c.update(g.update("直走",true,100),scene(100,"left","行人"),100));
        assertEquals("停止",c.update(g.update("直走",true,1000),scene(1000,"left","行人"),1000));
        assertEquals("停止",c.update(g.update("直走",true,1599),scene(1599,"left","行人"),1599));
        assertEquals("直走，左侧有行人",c.update(g.update("直走",true,1600),scene(1600,"left","行人"),1600));
    }
}
