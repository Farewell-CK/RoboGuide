//! Symlink-safe filesystem path validation and durable mutation primitives.

use crate::types::ArtifactStoreError;
use std::fs::{self, File, OpenOptions};
use std::io;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

/// Validates an upload identifier against the staging filename grammar.
pub(crate) fn validate_upload_id(value: String) -> Result<String, ArtifactStoreError> {
    let valid = !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'));
    if valid {
        Ok(value)
    } else {
        Err(ArtifactStoreError::InvalidUploadId { value })
    }
}

/// Creates a directory tree while rejecting symlink and non-directory components.
///
/// Each new entry is durably recorded before later artifact publication can depend on it.
pub(crate) fn create_directory(
    path: &Path,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    let path = std::path::absolute(path).map_err(|error| io_error(operation, error))?;
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&metadata, operation)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                match fs::create_dir(&current) {
                    Ok(()) => {
                        let parent = current
                            .parent()
                            .filter(|parent| !parent.as_os_str().is_empty())
                            .unwrap_or_else(|| Path::new("."));
                        sync_directory(parent, "sync artifact parent directory")?;
                        sync_directory(&current, "sync created artifact directory")?;
                    }
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                        let metadata = fs::symlink_metadata(&current)
                            .map_err(|error| io_error(operation, error))?;
                        validate_directory_component(&metadata, operation)?;
                    }
                    Err(error) => return Err(io_error(operation, error)),
                }
            }
            Err(error) => return Err(io_error(operation, error)),
        }
    }
    Ok(())
}

/// Validates all existing path components without following symbolic links.
///
/// When `allow_missing` is true, the first absent component and its descendants are accepted so
/// read-only lookup can report an absent digest without creating directories.
pub(crate) fn validate_directory_tree(
    path: &Path,
    allow_missing: bool,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    let path = std::path::absolute(path).map_err(|error| io_error(operation, error))?;
    let mut current = PathBuf::new();
    let mut missing = false;
    for component in path.components() {
        current.push(component.as_os_str());
        if missing {
            continue;
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&metadata, operation)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound && allow_missing => {
                missing = true;
            }
            Err(error) => return Err(io_error(operation, error)),
        }
    }
    Ok(())
}

/// Rejects one path component unless it is a real directory rather than a symbolic link.
fn validate_directory_component(
    metadata: &fs::Metadata,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(unsafe_path_error(
            operation,
            "artifact directory component must be a directory and cannot be a symbolic link",
        ));
    }
    Ok(())
}

/// Validates the parent directory of one digest-derived blob path.
pub(crate) fn validate_blob_parent(
    path: &Path,
    allow_missing: bool,
) -> Result<(), ArtifactStoreError> {
    let parent = path.parent().expect("digest blob path always has a parent");
    validate_directory_tree(parent, allow_missing, "validate artifact blob directory")
}

/// Rejects an absent, symbolic-link, or non-regular artifact file path.
pub(crate) fn require_regular_file(
    path: &Path,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| io_error(operation, error))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(unsafe_path_error(
            operation,
            "artifact path must be a regular file and cannot be a symbolic link",
        ));
    }
    Ok(())
}

/// Opens a regular artifact file without following a symbolic-link leaf on Unix.
pub(crate) fn open_regular_file(
    path: &Path,
    operation: &'static str,
) -> Result<File, ArtifactStoreError> {
    let mut options = OpenOptions::new();
    options.read(true);
    add_no_follow_flag(&mut options);
    let file = options
        .open(path)
        .map_err(|error| io_error(operation, error))?;
    if !file
        .metadata()
        .map_err(|error| io_error(operation, error))?
        .is_file()
    {
        return Err(unsafe_path_error(
            operation,
            "artifact path must resolve to a regular file",
        ));
    }
    Ok(file)
}

/// Adds the Unix no-follow flag while leaving non-Unix path checks to `symlink_metadata`.
pub(crate) fn add_no_follow_flag(options: &mut OpenOptions) {
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    #[cfg(not(unix))]
    let _ = options;
}

/// Removes a staging path while treating an already absent path as success.
pub(crate) fn remove_staging(path: &Path) -> Result<(), ArtifactStoreError> {
    if let Some(parent) = path.parent() {
        validate_directory_tree(parent, false, "validate artifact staging directory")?;
    }
    match fs::remove_file(path) {
        Ok(()) => {
            if let Some(parent) = path.parent() {
                sync_directory(parent, "sync artifact staging removal")?;
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(io_error("remove artifact staging file", error)),
    }
}

/// Removes incomplete upload files left behind by a previous store process.
///
/// Upload handles are intentionally not recoverable because their incremental hash state is
/// process-local. Initialization therefore removes only `.partial` leaves from the validated
/// staging directory and refuses to recurse through an unexpected directory entry.
pub(crate) fn cleanup_abandoned_staging(
    staging_directory: &Path,
) -> Result<(), ArtifactStoreError> {
    validate_directory_tree(
        staging_directory,
        false,
        "validate artifact staging directory",
    )?;
    let entries = fs::read_dir(staging_directory)
        .map_err(|error| io_error("scan artifact staging directory", error))?;
    for entry in entries {
        let entry = entry.map_err(|error| io_error("read artifact staging entry", error))?;
        let file_name = entry.file_name();
        if !file_name.to_string_lossy().ends_with(".partial") {
            continue;
        }
        let metadata = fs::symlink_metadata(entry.path())
            .map_err(|error| io_error("inspect abandoned artifact upload", error))?;
        if metadata.is_dir() && !metadata.file_type().is_symlink() {
            return Err(unsafe_path_error(
                "clean abandoned artifact upload",
                "staging partial path cannot be a directory",
            ));
        }
        remove_staging(&entry.path())?;
    }
    Ok(())
}

/// Removes write permission from one verified finalized blob.
pub(crate) fn seal_blob(path: &Path) -> Result<(), ArtifactStoreError> {
    validate_blob_parent(path, false)?;
    require_regular_file(path, "inspect artifact for sealing")?;
    let file = open_regular_file(path, "open artifact for sealing")?;
    let mut permissions = file
        .metadata()
        .map_err(|error| io_error("inspect finalized artifact permissions", error))?
        .permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions)
        .map_err(|error| io_error("seal finalized artifact", error))?;
    file.sync_all()
        .map_err(|error| io_error("sync sealed artifact", error))
}

/// Flushes one directory so acknowledged link creation or removal survives power loss.
pub(crate) fn sync_directory(
    path: &Path,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    validate_directory_tree(path, false, operation)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY);
    let directory = options
        .open(path)
        .map_err(|error| io_error(operation, error))?;
    directory
        .sync_all()
        .map_err(|error| io_error(operation, error))
}

/// Builds a stable invalid-data error for a filesystem entry that violates CAS confinement.
pub(crate) fn unsafe_path_error(
    operation: &'static str,
    reason: &'static str,
) -> ArtifactStoreError {
    io_error(
        operation,
        io::Error::new(io::ErrorKind::InvalidData, reason),
    )
}

/// Wraps one operating-system error with a stable artifact operation label.
pub(crate) fn io_error(operation: &'static str, source: io::Error) -> ArtifactStoreError {
    ArtifactStoreError::Io { operation, source }
}
