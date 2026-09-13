package com.seaway.guideassistant.llm

object NavigationIntentValidator {
    private val allowedIntents = setOf("navigation", "cancel", "query", "unknown")
    private val allowedModes = setOf("outdoor", "indoor")

    fun validate(result: NavigationIntent) {
        require(result.intent in allowedIntents) { "Unsupported intent: ${result.intent}" }
        result.confidence?.let { require(it in 0.0..1.0) { "confidence must be in [0, 1]" } }

        require(result.totalPhases == result.navigationPhases.size) {
            "total_phases must equal navigation_phases.size"
        }

        if (result.navigationPhases.isEmpty()) {
            require(result.currentPhase == 0) { "empty phases require current_phase=0" }
        } else {
            require(result.intent == "navigation") { "only navigation may contain phases" }
            require(result.currentPhase == 1) { "non-empty phases require current_phase=1" }
            result.navigationPhases.forEachIndexed { index, phase ->
                require(phase.phase == index + 1) { "phase numbers must start at 1 and be continuous" }
                require(phase.mode in allowedModes) { "Unsupported phase mode: ${phase.mode}" }
                require(phase.description.isNotBlank()) { "phase description cannot be blank" }
                phase.floor?.let { require(it > 0) { "floor must be positive when present" } }
            }
        }

        if (result.intent != "navigation") {
            require(result.navigationPhases.isEmpty()) { "non-navigation intent must not contain phases" }
        }

        if (result.needClarification) {
            require(result.clarificationQuestion?.isNotBlank() == true) {
                "need_clarification=true requires clarification_question"
            }
        }
    }
}
