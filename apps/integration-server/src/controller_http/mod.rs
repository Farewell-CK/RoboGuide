//! Controller HTTP composition by transport and projection responsibility.

mod protocol;
mod server;
mod view;

pub(crate) use protocol::*;
pub(crate) use server::*;
pub(crate) use view::*;
