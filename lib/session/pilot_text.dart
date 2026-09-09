/// Pure Pilot-text accumulation shared by the app and the replay CLI (no Flutter).
library;

/// Merge a new Pilot text event into the accumulated assistant text.
///
/// Pilot streams a narration as incremental chunks and closes the turn with a
/// `final_text`. The final_text can be (a) the full accumulated text, or (b) a
/// repeat of the last narration segment — both would duplicate a naive `+=`.
/// Chunks are continuous narration (no separator), so we only special-case the
/// overlap-heavy cases and otherwise append directly:
///  - incoming is the full final (contains current) → take incoming
///  - current already contains the incoming segment (final repeats it) → keep
///  - otherwise it's the next chunk → append directly (no separator).
String mergePilotText(String current, String incoming) {
  if (incoming.isEmpty) return current;
  if (current.isEmpty) return incoming;
  if (incoming.contains(current)) return incoming;
  if (current.contains(incoming)) return current;
  return current + incoming;
}