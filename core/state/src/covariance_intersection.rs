//! Bounded deterministic covariance intersection for explicitly declared uncertain numeric vectors.

/// One Gaussian estimate with a covariance, in an externally checked common unit and frame.
#[derive(Debug, Clone, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct GaussianEstimate {
    /// Source-owned mean vector, never derived from a confidence score.
    pub mean: Vec<f64>,
    /// Symmetric positive-definite covariance in squared source units.
    pub covariance: Vec<Vec<f64>>,
    /// Exact common unit.
    pub unit: String,
    /// Exact common reference frame.
    pub frame: String,
}

/// Conservative fused estimate plus the actual deterministic convex weights used at each fold.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct CovarianceIntersectionResult {
    /// Result under unknown cross-correlation; correctness still assumes valid source covariance bounds.
    pub estimate: GaussianEstimate,
    /// Previous aggregate weight for each subsequent source, in unchanged deterministic source order.
    pub weights: Vec<f64>,
}

/// Computes bounded sequential CI, minimizing covariance trace over an explicit convex weight grid.
pub fn covariance_intersection(
    estimates: &[GaussianEstimate],
    weight_steps: u8,
) -> Result<CovarianceIntersectionResult, String> {
    if estimates.is_empty() || estimates.len() > 8 || !(1..=32).contains(&weight_steps) {
        return Err("covariance intersection source/search budget is invalid".into());
    }
    let mut aggregate = estimates[0].clone();
    validate_estimate(&aggregate)?;
    let mut weights = Vec::new();
    for next in &estimates[1..] {
        validate_estimate(next)?;
        if next.mean.len() != aggregate.mean.len()
            || next.unit != aggregate.unit
            || next.frame != aggregate.frame
        {
            return Err("Gaussian source unit/frame/dimensions conflict".into());
        }
        let left_information = inverse_spd(&aggregate.covariance)?;
        let right_information = inverse_spd(&next.covariance)?;
        let left_mean = multiply(&left_information, &aggregate.mean);
        let right_mean = multiply(&right_information, &next.mean);
        let mut best: Option<(f64, f64, GaussianEstimate)> = None;
        for ordinal in 0..=weight_steps {
            let weight = f64::from(ordinal) / f64::from(weight_steps);
            let information = left_information
                .iter()
                .zip(&right_information)
                .map(|(left, right)| {
                    left.iter()
                        .zip(right)
                        .map(|(a, b)| weight * a + (1.0 - weight) * b)
                        .collect()
                })
                .collect::<Vec<_>>();
            let covariance = inverse_spd(&information)?;
            let information_mean = left_mean
                .iter()
                .zip(&right_mean)
                .map(|(a, b)| weight * a + (1.0 - weight) * b)
                .collect::<Vec<_>>();
            let mean = multiply(&covariance, &information_mean);
            let score = covariance
                .iter()
                .enumerate()
                .map(|(index, row)| row[index])
                .sum::<f64>();
            if !score.is_finite() || mean.iter().any(|value| !value.is_finite()) {
                return Err("covariance intersection numeric range is unavailable".into());
            }
            // Equal trace preserves the first deterministic grid weight; no stochastic tie breaker.
            if best
                .as_ref()
                .is_none_or(|(previous, _, _)| score < *previous)
            {
                best = Some((
                    score,
                    weight,
                    GaussianEstimate {
                        mean,
                        covariance,
                        unit: aggregate.unit.clone(),
                        frame: aggregate.frame.clone(),
                    },
                ));
            }
        }
        let (_, weight, estimate) =
            best.ok_or("covariance intersection produced no finite estimate")?;
        aggregate = estimate;
        weights.push(weight);
    }
    Ok(CovarianceIntersectionResult {
        estimate: aggregate,
        weights,
    })
}

/// Rejects unknown, malformed or numerically singular covariance without inventing regularization noise.
fn validate_estimate(estimate: &GaussianEstimate) -> Result<(), String> {
    let dimension = estimate.mean.len();
    if !(1..=6).contains(&dimension)
        || estimate.mean.iter().any(|value| !value.is_finite())
        || estimate.unit.trim().is_empty()
        || estimate.unit.len() > 128
        || estimate.frame.trim().is_empty()
        || estimate.frame.len() > 128
        || estimate.covariance.len() != dimension
    {
        return Err("Gaussian dimensions/units/finite mean are invalid".into());
    }
    cholesky(&estimate.covariance).map(|_| ())
}

/// Solves symmetric positive-definite systems through a checked Cholesky decomposition.
#[allow(clippy::needless_range_loop)] // Triangular solves and symmetry need both row and column indices.
fn inverse_spd(matrix: &[Vec<f64>]) -> Result<Vec<Vec<f64>>, String> {
    let lower = cholesky(matrix)?;
    let n = lower.len();
    let mut inverse = vec![vec![0.0; n]; n];
    for column in 0..n {
        let mut intermediate = vec![0.0; n];
        for row in 0..n {
            let mut value = f64::from(row == column);
            for (index, known) in intermediate.iter().enumerate().take(row) {
                value -= lower[row][index] * known;
            }
            intermediate[row] = value / lower[row][row];
        }
        let mut solution = vec![0.0; n];
        for row in (0..n).rev() {
            let mut value = intermediate[row];
            for (index, known) in solution.iter().enumerate().skip(row + 1) {
                value -= lower[index][row] * known;
            }
            solution[row] = value / lower[row][row];
        }
        for row in 0..n {
            inverse[row][column] = solution[row];
        }
    }
    // Roundoff symmetry is explicit; this does not alter the covariance's declared noise model.
    for row in 0..n {
        for column in 0..row {
            let average = (inverse[row][column] + inverse[column][row]) / 2.0;
            inverse[row][column] = average;
            inverse[column][row] = average;
        }
    }
    if inverse.iter().flatten().any(|value| !value.is_finite()) {
        return Err("Gaussian inverse is outside finite numeric bounds".into());
    }
    Ok(inverse)
}

/// Decomposes only bounded finite symmetric matrices, rejecting nonpositive or ill-conditioned pivots.
fn cholesky(matrix: &[Vec<f64>]) -> Result<Vec<Vec<f64>>, String> {
    let n = matrix.len();
    if !(1..=6).contains(&n)
        || matrix
            .iter()
            .any(|row| row.len() != n || row.iter().any(|v| !v.is_finite()))
    {
        return Err("Gaussian covariance matrix is invalid".into());
    }
    let scale = matrix
        .iter()
        .enumerate()
        .map(|(index, row)| row[index].abs())
        .fold(0.0_f64, f64::max);
    if scale <= 0.0 || !scale.is_finite() {
        return Err("Gaussian covariance has no positive scale".into());
    }
    let mut lower = vec![vec![0.0; n]; n];
    for row in 0..n {
        for column in 0..=row {
            if (matrix[row][column] - matrix[column][row]).abs() > scale * 1e-10 {
                return Err("Gaussian covariance is not symmetric".into());
            }
            let mut value = matrix[row][column];
            value -= lower[row][..column]
                .iter()
                .zip(&lower[column][..column])
                .map(|(left, right)| left * right)
                .sum::<f64>();
            if row == column {
                if value <= scale * 1e-12 || !value.is_finite() {
                    return Err("Gaussian covariance is not stably positive definite".into());
                }
                lower[row][column] = value.sqrt();
            } else {
                lower[row][column] = value / lower[column][column];
            }
        }
    }
    Ok(lower)
}

/// Multiplies one validated bounded matrix and vector without changing coordinate conventions.
fn multiply(matrix: &[Vec<f64>], vector: &[f64]) -> Vec<f64> {
    matrix
        .iter()
        .map(|row| row.iter().zip(vector).map(|(a, b)| a * b).sum())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Declares two independent mathematical input files' equivalent typed Gaussian values.
    fn estimate(mean: &[f64], diagonal: &[f64]) -> GaussianEstimate {
        GaussianEstimate {
            mean: mean.to_vec(),
            covariance: diagonal
                .iter()
                .enumerate()
                .map(|(i, v)| {
                    (0..diagonal.len())
                        .map(|j| if i == j { *v } else { 0.0 })
                        .collect()
                })
                .collect(),
            unit: "m".into(),
            frame: "survey".into(),
        }
    }

    /// Repeated correlated evidence cannot double information or shrink its original covariance.
    #[test]
    fn repeated_evidence_retains_covariance() {
        let original = estimate(&[2.0, 3.0], &[1.0, 9.0]);
        let fused =
            covariance_intersection(&[original.clone(), original.clone(), original.clone()], 32)
                .unwrap();
        for (actual, expected) in fused
            .estimate
            .covariance
            .iter()
            .flatten()
            .zip(original.covariance.iter().flatten())
        {
            assert!((actual - expected).abs() < 1e-12);
        }
    }

    /// The closed-form symmetric two-dimensional solution independently checks the bounded optimizer.
    #[test]
    fn complementary_axes_match_closed_form() {
        let fused = covariance_intersection(
            &[
                estimate(&[0.0, 0.0], &[1.0, 9.0]),
                estimate(&[2.0, 2.0], &[9.0, 1.0]),
            ],
            32,
        )
        .unwrap();
        assert_eq!(fused.weights, [0.5]);
        assert!((fused.estimate.covariance[0][0] - 1.8).abs() < 1e-12);
        assert!((fused.estimate.covariance[1][1] - 1.8).abs() < 1e-12);
        assert!((fused.estimate.mean[0] - 0.2).abs() < 1e-12);
        assert!((fused.estimate.mean[1] - 1.8).abs() < 1e-12);
    }

    /// Unknown correlation never permits missing covariance, mismatched units or silently repaired matrices.
    #[test]
    fn invalid_covariance_and_coordinate_mismatch_reject() {
        let valid = estimate(&[1.0, 2.0], &[1.0, 1.0]);
        let mut invalid = valid.clone();
        invalid.covariance[0][1] = 4.0;
        invalid.covariance[1][0] = 4.0;
        assert!(covariance_intersection(&[invalid], 16).is_err());
        let mut invalid = valid.clone();
        invalid.covariance[1][1] = 0.0;
        assert!(covariance_intersection(&[invalid], 16).is_err());
        let mut mismatch = valid.clone();
        mismatch.unit = "cm".into();
        assert!(covariance_intersection(&[valid.clone(), mismatch], 16).is_err());
        let mut mismatch = valid.clone();
        mismatch.frame = "unregistered".into();
        assert!(covariance_intersection(&[valid, mismatch], 16).is_err());
    }
}
