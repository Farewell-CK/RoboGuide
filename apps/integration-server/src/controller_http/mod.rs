//! Controller HTTP composition by transport and projection responsibility.

mod protocol;
mod server;
mod view;

pub(crate) use server::serve_http;

#[cfg(test)]
pub(crate) use protocol::{parse_query, read_control_http_request};
#[cfg(test)]
pub(crate) use server::handle_http_connection;
#[cfg(test)]
pub(crate) use view::{inventory_json, state_record_matches, state_view_record};
