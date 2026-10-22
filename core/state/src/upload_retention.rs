//! Independent completed-upload release validation shared by Catalog and content-maintenance closure.

use domain::{ArtifactUploadRecord, ArtifactUploadRetirement};
use std::collections::BTreeMap;

/// Checks the exact original upload and immutable command before a retirement may be committed/replayed.
pub fn validate_upload_retirement(
    uploads: &BTreeMap<String, ArtifactUploadRecord>,
    retirements: &BTreeMap<String, ArtifactUploadRetirement>,
    receipt: &ArtifactUploadRetirement,
) -> Result<(), String> {
    receipt.validate()?;
    if uploads.get(&receipt.upload.upload_id) != Some(&receipt.upload) {
        return Err("upload retirement lacks its exact original completed upload".into());
    }
    if retirements
        .values()
        .any(|original| original.command_id == receipt.command_id && original != receipt)
        || retirements
            .get(&receipt.upload.upload_id)
            .is_some_and(|original| original != receipt)
    {
        return Err("upload retirement changes its original command, receipt or upload".into());
    }
    Ok(())
}
