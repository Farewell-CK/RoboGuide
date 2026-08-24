#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Integration adapters that translate transport and Local EAIOS details at the core boundary.

pub mod bridge;
pub mod http;
