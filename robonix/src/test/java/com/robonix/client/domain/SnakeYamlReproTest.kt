package com.robonix.client.domain

import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Repro for the device-only `LinkedHashMap cannot be cast to Void` failure in
 * `RobotVitalsMapper.normalizeRobotDescription` (yaml.load at line 68).
 * Uses the real soma YAML captured from the robot.
 */
class SnakeYamlReproTest {
    private fun yamlText(): String {
        val stream = checkNotNull(javaClass.getResourceAsStream("/simplebot.yaml")) {
            "missing simplebot.yaml test resource"
        }
        return stream.bufferedReader().use { it.readText() }
    }

    @Test
    fun normalizeRobotDescriptionParsesRealSomaYaml() {
        val desc = RobotVitalsMapper.normalizeRobotDescription(yamlText())
        println("OK id=${desc.id} components=${desc.components.map { it.id }} render=${desc.renderMode}")
        assertTrue(desc.components.isNotEmpty())
        assertTrue(desc.components.any { it.id == "body" })
    }
}
