package com.seaway.guideassistant.llm

/**
 * 很小的 JSON parser/stringifier，避免这个意图模块为了一个固定 schema 引入额外 JSON 依赖。
 * 支持 object / array / string / number / boolean / null，足够处理 OpenAI-compatible response。
 */
internal object SimpleJson {
    fun parse(text: String): Any? = Parser(text).parse()

    fun stringify(value: Any?): String = when (value) {
        null -> "null"
        is String -> quote(value)
        is Boolean -> value.toString()
        is Byte, is Short, is Int, is Long -> value.toString()
        is Float -> {
            require(value.isFinite()) { "JSON does not support non-finite numbers" }
            value.toString()
        }
        is Double -> {
            require(value.isFinite()) { "JSON does not support non-finite numbers" }
            value.toString()
        }
        is Map<*, *> -> value.entries.joinToString(prefix = "{", postfix = "}") { (k, v) ->
            require(k is String) { "JSON object keys must be strings" }
            "${quote(k)}:${stringify(v)}"
        }
        is Iterable<*> -> value.joinToString(prefix = "[", postfix = "]") { stringify(it) }
        is Array<*> -> value.joinToString(prefix = "[", postfix = "]") { stringify(it) }
        else -> error("Unsupported JSON value type: ${value::class.java.name}")
    }

    private fun quote(value: String): String = buildString {
        append('"')
        value.forEach { ch ->
            when (ch) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\u000C' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (ch.code < 0x20) append("\\u%04x".format(ch.code)) else append(ch)
            }
        }
        append('"')
    }

    private class Parser(private val text: String) {
        private var index = 0

        fun parse(): Any? {
            skipWhitespace()
            val result = parseValue()
            skipWhitespace()
            require(index == text.length) { "Unexpected trailing JSON content at index $index" }
            return result
        }

        private fun parseValue(): Any? {
            skipWhitespace()
            require(index < text.length) { "Unexpected end of JSON" }
            return when (text[index]) {
                '{' -> parseObject()
                '[' -> parseArray()
                '"' -> parseString()
                't' -> parseLiteral("true", true)
                'f' -> parseLiteral("false", false)
                'n' -> parseLiteral("null", null)
                '-', in '0'..'9' -> parseNumber()
                else -> error("Unexpected JSON character '${text[index]}' at index $index")
            }
        }

        private fun parseObject(): Map<String, Any?> {
            expect('{')
            skipWhitespace()
            val result = linkedMapOf<String, Any?>()
            if (peek('}')) {
                index++
                return result
            }
            while (true) {
                skipWhitespace()
                require(peek('"')) { "Expected object key at index $index" }
                val key = parseString()
                skipWhitespace()
                expect(':')
                val value = parseValue()
                result[key] = value
                skipWhitespace()
                when {
                    peek(',') -> index++
                    peek('}') -> {
                        index++
                        return result
                    }
                    else -> error("Expected ',' or '}' at index $index")
                }
            }
        }

        private fun parseArray(): List<Any?> {
            expect('[')
            skipWhitespace()
            val result = mutableListOf<Any?>()
            if (peek(']')) {
                index++
                return result
            }
            while (true) {
                result += parseValue()
                skipWhitespace()
                when {
                    peek(',') -> index++
                    peek(']') -> {
                        index++
                        return result
                    }
                    else -> error("Expected ',' or ']' at index $index")
                }
            }
        }

        private fun parseString(): String {
            expect('"')
            val out = StringBuilder()
            while (index < text.length) {
                val ch = text[index++]
                when (ch) {
                    '"' -> return out.toString()
                    '\\' -> {
                        require(index < text.length) { "Unfinished escape sequence" }
                        when (val esc = text[index++]) {
                            '"' -> out.append('"')
                            '\\' -> out.append('\\')
                            '/' -> out.append('/')
                            'b' -> out.append('\b')
                            'f' -> out.append('\u000C')
                            'n' -> out.append('\n')
                            'r' -> out.append('\r')
                            't' -> out.append('\t')
                            'u' -> {
                                require(index + 4 <= text.length) { "Invalid unicode escape" }
                                val hex = text.substring(index, index + 4)
                                out.append(hex.toInt(16).toChar())
                                index += 4
                            }
                            else -> error("Unsupported escape \\$esc")
                        }
                    }
                    else -> out.append(ch)
                }
            }
            error("Unterminated JSON string")
        }

        private fun parseNumber(): Number {
            val start = index
            if (peek('-')) index++
            if (peek('0')) {
                index++
            } else {
                require(index < text.length && text[index].isDigit()) { "Invalid number at index $index" }
                while (index < text.length && text[index].isDigit()) index++
            }
            var decimal = false
            if (peek('.')) {
                decimal = true
                index++
                require(index < text.length && text[index].isDigit()) { "Invalid decimal number" }
                while (index < text.length && text[index].isDigit()) index++
            }
            if (index < text.length && (text[index] == 'e' || text[index] == 'E')) {
                decimal = true
                index++
                if (index < text.length && (text[index] == '+' || text[index] == '-')) index++
                require(index < text.length && text[index].isDigit()) { "Invalid exponent" }
                while (index < text.length && text[index].isDigit()) index++
            }
            val token = text.substring(start, index)
            return if (decimal) token.toDouble() else token.toLong()
        }

        private fun <T> parseLiteral(literal: String, value: T): T {
            require(text.startsWith(literal, index)) { "Expected '$literal' at index $index" }
            index += literal.length
            return value
        }

        private fun skipWhitespace() {
            while (index < text.length && text[index].isWhitespace()) index++
        }

        private fun expect(ch: Char) {
            require(index < text.length && text[index] == ch) { "Expected '$ch' at index $index" }
            index++
        }

        private fun peek(ch: Char): Boolean = index < text.length && text[index] == ch
    }
}
