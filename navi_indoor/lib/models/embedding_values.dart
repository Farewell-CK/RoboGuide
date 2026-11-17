import 'dart:convert';

/// PostgreSQL JSON can contain both integer and floating point numbers.
List<double> parseNumericValues(dynamic value) {
  final data = value is String ? jsonDecode(value) : value;
  if (data is! List) throw const FormatException('Invalid numeric array');
  return data.map<double>((item) {
    if (item is! num || !item.toDouble().isFinite) {
      throw const FormatException('Invalid numeric value');
    }
    return item.toDouble();
  }).toList();
}

bool isUsableEmbedding(List<double> values) =>
    values.length == 768 &&
    values.every((v) => v.isFinite) &&
    values.any((v) => v != 0);
