//! Exercise the maintained SQLite store in a separate process; never create Node/device facts.

use controller_replication::SqliteRaftStore;

/// Open the explicitly declared store, report readiness and retain its real leases until stdin.
fn main() {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.len() != 3 {
        eprintln!("usage: storage_lease_probe DATABASE PROFILE APPLICATION_SCHEMA");
        std::process::exit(2);
    }
    match SqliteRaftStore::open(
        std::path::Path::new(&arguments[0]),
        &arguments[1],
        &arguments[2],
    ) {
        Ok(store) => {
            println!("opened");
            let mut line = String::new();
            std::io::stdin().read_line(&mut line).unwrap();
            drop(store);
        }
        Err(error) => println!("refused: {error}"),
    }
}
