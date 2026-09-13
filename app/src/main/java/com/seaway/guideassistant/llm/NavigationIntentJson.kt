package com.seaway.guideassistant.llm

internal object NavigationIntentJson {
    fun decode(value: Any?): NavigationIntent {
        val obj = value.asObject("intent result")
        val phases = obj["navigation_phases"]
            ?.asArray("navigation_phases")
            ?.map { decodePhase(it) }
            ?: emptyList()

        return NavigationIntent(
            intent = obj.requiredString("intent"),
            destination = obj.nullableString("destination"),
            navigationPhases = phases,
            currentPhase = obj.intOrDefault("current_phase", 0),
            totalPhases = obj.intOrDefault("total_phases", phases.size),
            needClarification = obj.booleanOrDefault("need_clarification", false),
            clarificationQuestion = obj.nullableString("clarification_question"),
            confidence = obj.nullableDouble("confidence")
        )
    }

    private fun decodePhase(value: Any?): NavigationPhase {
        val obj = value.asObject("navigation phase")
        return NavigationPhase(
            phase = obj.requiredInt("phase"),
            mode = obj.requiredString("mode"),
            description = obj.requiredString("description"),
            floor = obj.nullableInt("floor")
        )
    }

    private fun Any?.asObject(name: String): Map<*, *> =
        this as? Map<*, *> ?: error("$name must be a JSON object")

    private fun Any?.asArray(name: String): List<*> =
        this as? List<*> ?: error("$name must be a JSON array")

    private fun Map<*, *>.requiredString(key: String): String =
        this[key] as? String ?: error("$key must be a string")

    private fun Map<*, *>.nullableString(key: String): String? = when (val value = this[key]) {
        null -> null
        is String -> value
        else -> error("$key must be a string or null")
    }

    private fun Map<*, *>.requiredInt(key: String): Int =
        nullableInt(key) ?: error("$key must be an integer")

    private fun Map<*, *>.nullableInt(key: String): Int? = when (val value = this[key]) {
        null -> null
        is Number -> {
            val longValue = value.toLong()
            require(value.toDouble() == longValue.toDouble()) { "$key must be an integer" }
            require(longValue in Int.MIN_VALUE..Int.MAX_VALUE) { "$key is out of Int range" }
            longValue.toInt()
        }
        else -> error("$key must be an integer or null")
    }

    private fun Map<*, *>.intOrDefault(key: String, default: Int): Int =
        if (containsKey(key)) nullableInt(key) ?: error("$key cannot be null") else default

    private fun Map<*, *>.booleanOrDefault(key: String, default: Boolean): Boolean = when (val value = this[key]) {
        null -> if (containsKey(key)) error("$key cannot be null") else default
        is Boolean -> value
        else -> error("$key must be a boolean")
    }

    private fun Map<*, *>.nullableDouble(key: String): Double? = when (val value = this[key]) {
        null -> null
        is Number -> value.toDouble()
        else -> error("$key must be a number or null")
    }
}
