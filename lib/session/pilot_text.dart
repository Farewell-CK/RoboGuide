/// Pure Pilot-text accumulation shared by the app and the replay CLI (no Flutter).
library;

/// Merge a new Pilot text event into the accumulated assistant text.
///
/// Pilot streams a long answer as `text_chunk`s and ends with a `final_text`
/// that usually contains the full accumulated text. Naive `+=` would repeat
/// the whole answer when the final event arrives. Chunks are continuous
/// narration (no separator), so the only replace-able case is the final text
/// that already contains everything we accumulated — take it as-is:
///  - incoming is the full final (contains current) → take incoming
///  - otherwise it's the next chunk (or a disjoint addendum) → append directly.
String mergePilotText(String current, String incoming) {
  if (incoming.isEmpty) return current;
  if (current.isEmpty) return incoming;
  if (incoming.contains(current)) return incoming;
  return current + incoming;
}