const double departureHeadingTolerance = 5.0;

double normalizeNavigationHeading(double heading) => heading % 360;

/// Clockwise is right, counter-clockwise is left. Cross north by the short arc.
double relativeTargetHeading(double current, double target) {
  final difference = (target - current + 180) % 360 - 180;
  return difference;
}

bool isDepartureHeadingAligned(double current, double target) =>
    current.isFinite &&
    target.isFinite &&
    relativeTargetHeading(current, target).abs() <= departureHeadingTolerance;
