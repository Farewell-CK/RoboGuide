package com.seaway.guideassistant.llm

object NavigationIntentEncoder {
    fun toJson(intent: NavigationIntent): String = SimpleJson.stringify(
        linkedMapOf<String, Any?>(
            "intent" to intent.intent,
            "destination" to intent.destination,
            "navigation_phases" to intent.navigationPhases.map { phase ->
                linkedMapOf<String, Any?>(
                    "phase" to phase.phase,
                    "mode" to phase.mode,
                    "description" to phase.description
                ).apply {
                    phase.floor?.let { put("floor", it) }
                }
            },
            "current_phase" to intent.currentPhase,
            "total_phases" to intent.totalPhases,
            "need_clarification" to intent.needClarification,
            "clarification_question" to intent.clarificationQuestion,
            "confidence" to intent.confidence
        )
    )
}
