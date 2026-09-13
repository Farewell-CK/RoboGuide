package com.robonix.client.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val RobonixDarkColors = darkColorScheme(
    primary = Blue,
    onPrimary = Bg,
    secondary = Cyan,
    onSecondary = Bg,
    tertiary = Green,
    onTertiary = Bg,
    error = Red,
    onError = Text,
    background = Bg,
    onBackground = Text,
    surface = Panel,
    onSurface = Text,
    surfaceVariant = Panel2,
    onSurfaceVariant = Muted,
    outline = Line,
    outlineVariant = LineSoft,
)

val RobonixTypography = Typography(
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 12.sp,
        lineHeight = 17.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 13.sp,
        lineHeight = 18.5.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 17.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 15.sp,
        lineHeight = 19.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.ExtraBold,
        fontSize = 11.sp,
        lineHeight = 13.sp,
        letterSpacing = 0.5.sp,
    ),
)

@Composable
fun RobonixTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = RobonixDarkColors,
        typography = RobonixTypography,
        content = content,
    )
}
