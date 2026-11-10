package com.seaway.guideassistant.navindoor

/** Independent indoor navigation must not overlap an existing navigation task. */
object NaviEntryPolicy {
    fun canOpen(routePending: Boolean, archiveActive: Boolean, navigationActive: Boolean): Boolean =
        !routePending && !archiveActive && !navigationActive
}
