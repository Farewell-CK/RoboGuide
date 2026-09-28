package com.seaway.guideassistant.utils

/**
 * 登录态存储，基于MMKVUtils，对应prototype/app.js中的loadSession/saveSession/clearSession
 */
object SessionManager {
    private const val KEY_NAME = "guide_user_name"
    private const val KEY_PHONE = "guide_user_phone"
    private const val KEY_LOGGED_IN = "guide_user_logged_in"

    fun isLoggedIn(): Boolean = MMKVUtils.getAny(KEY_LOGGED_IN, false) as Boolean

    fun saveSession(name: String, phone: String) {
        MMKVUtils.putAny(KEY_NAME, name)
        MMKVUtils.putAny(KEY_PHONE, phone)
        MMKVUtils.putAny(KEY_LOGGED_IN, true)
    }

    fun clearSession() {
        MMKVUtils.putAny(KEY_LOGGED_IN, false)
    }

    fun getUserName(): String = MMKVUtils.getAny(KEY_NAME, "") as String

    fun getUserPhone(): String = MMKVUtils.getAny(KEY_PHONE, "") as String
}
