package com.seaway.guideassistant.llm

data class NavigationIntent(
    val intent: String,
    val destination: String? = null,
    val navigationPhases: List<NavigationPhase> = emptyList(),
    val currentPhase: Int = 0,
    val totalPhases: Int = 0,
    val needClarification: Boolean = false,
    val clarificationQuestion: String? = null,
    val confidence: Double? = null
)

data class NavigationPhase(
    val phase: Int,
    val mode: String,
    val description: String,
    val floor: Int? = null
)
