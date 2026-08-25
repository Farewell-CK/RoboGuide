/// Generates the formal gRPC Node Protocol using a vendored protoc binary.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let protoc = protoc_bin_vendored::protoc_bin_path()?;
    let protos = ["../../contracts/node/v0.2/roboguide-node.proto"];
    for proto in protos {
        println!("cargo:rerun-if-changed={proto}");
    }
    let mut prost = prost_build::Config::new();
    prost.protoc_executable(protoc);
    tonic_prost_build::configure().compile_with_config(
        prost,
        &protos,
        &["../../contracts/node"],
    )?;
    Ok(())
}
