fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::compile_protos("../pagi-proto/pagi.proto")?;
    println!("cargo:rerun-if-changed=../pagi-proto/pagi.proto");
    Ok(())
}
