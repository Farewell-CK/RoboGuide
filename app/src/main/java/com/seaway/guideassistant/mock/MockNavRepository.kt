package com.seaway.guideassistant.mock

/**
 * 出行导航与避障静态演示数据，对应 prototype/app.js 的 navSteps / obstacleScenarios
 */
data class NavStep(
    val instruction: String,
    val label: String,
    val sub: String,
    val icon: String,
    val dir: String,
    val bearing: Int,
    val turnType: String,
    val caneVib: String,
    val caneHint: String,
)

data class ObstacleScenario(
    val title: String,
    val desc: String,
    val dir: String,
    val tags: List<String>,
)

object MockNavRepository {
    const val DEFAULT_DEST = "西单大悦城"

    val steps = listOf(
        NavStep(
            instruction = "请直行 200 米，沿长安街向东",
            label = "直行 200 米", sub = "沿长安街向东", icon = "🚶",
            dir = "forward", bearing = 90, turnType = "straight",
            caneVib = "前方长震 ×2", caneHint = "导盲杖前方连续震动，指示直行",
        ),
        NavStep(
            instruction = "前方路口右转，进入东单北大街",
            label = "右转进入东单北大街", sub = "路口语音提醒", icon = "↱",
            dir = "right", bearing = 180, turnType = "right",
            caneVib = "右侧短震 ×3", caneHint = "导盲杖右侧短震，提示右转",
        ),
        NavStep(
            instruction = "已到达地铁站，请乘坐地铁 1 号线，3 站后到达西单",
            label = "乘坐地铁 1 号线", sub = "天安门东 → 西单，3 站", icon = "🚇",
            dir = "forward", bearing = 90, turnType = "metro",
            caneVib = "前方间歇震", caneHint = "持杖跟随人流，前方间歇震动指引",
        ),
        NavStep(
            instruction = "已出站，请直行 150 米到达目的地",
            label = "出站后直行 150 米", sub = "到达目的地西单大悦城", icon = "🚶",
            dir = "forward", bearing = 45, turnType = "arrive",
            caneVib = "前方长震 ×2", caneHint = "导盲杖前方震动，指引出站方向",
        ),
    )

    val obstacleScenarios = listOf(
        ObstacleScenario(
            title = "正前方有台阶",
            desc = "建议向右偏移 30 厘米，导盲杖右侧短震（叠加方向指引）",
            dir = "right",
            tags = listOf("台阶 · 正前方 0.8m", "通道 · 右前方可通行"),
        ),
        ObstacleScenario(
            title = "左前方有行人",
            desc = "请减速，杖体左侧短震提醒，保持直行方向",
            dir = "left",
            tags = listOf("行人 · 左前方 1.2m", "通道 · 正前方可通行"),
        ),
        ObstacleScenario(
            title = "前方道路畅通",
            desc = "可正常前行，导盲杖持续指引前进方向",
            dir = "none",
            tags = listOf("通道 · 正前方可通行", "花坛 · 右侧 1.5m"),
        ),
        ObstacleScenario(
            title = "右前方有障碍物",
            desc = "建议向左绕行，杖体右侧长震，前进方向不变",
            dir = "right",
            tags = listOf("障碍物 · 右前方 0.6m", "通道 · 左前方可通行"),
        ),
    )

    fun vibDirLabel(dir: String): String = when (dir) {
        "left" -> "左侧 · 短震×3"
        "right" -> "右侧 · 短震×3"
        else -> "无避障震"
    }

    fun caneArrow(dir: String): String = when (dir) {
        "right" -> "↱"
        "left" -> "↰"
        "back" -> "↓"
        else -> "↑"
    }
}
