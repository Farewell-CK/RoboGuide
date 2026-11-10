package com.seaway.guideassistant.navindoor

import org.junit.Assert.assertEquals
import org.junit.Test

class NaviEntryPolicyTest {
    @Test fun onlyAnIdleHostCanLaunchIndoorNavigation() {
        for (pending in listOf(false, true)) {
            for (archive in listOf(false, true)) {
                for (plan in listOf(false, true)) {
                    assertEquals(!pending && !archive && !plan,
                        NaviEntryPolicy.canOpen(pending, archive, plan))
                }
            }
        }
    }
}
