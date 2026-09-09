package com.elabrador.mobilenavigation;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class VoiceCommandTest {
    @Test public void parsesDestinationAndCommands() {
        VoiceCommand destination = VoiceCommand.parse("请带我导航到 金水区政府。 ");
        assertEquals(VoiceCommand.Type.DESTINATION, destination.type);
        assertEquals("金水区政府", destination.value);
        assertEquals(VoiceCommand.Type.START, VoiceCommand.parse("开始导航").type);
        assertEquals(VoiceCommand.Type.END, VoiceCommand.parse("停止导航").type);
        assertEquals(VoiceCommand.Type.PAUSE, VoiceCommand.parse("暂停导航").type);
        assertEquals(VoiceCommand.Type.RESUME, VoiceCommand.parse("继续导航").type);
    }

    @Test public void acceptsSpokenAddressWithoutPrefix() {
        VoiceCommand command = VoiceCommand.parse("郑州市花园路一号");
        assertEquals(VoiceCommand.Type.DESTINATION, command.type);
        assertEquals("郑州市花园路一号", command.value);
    }
}
