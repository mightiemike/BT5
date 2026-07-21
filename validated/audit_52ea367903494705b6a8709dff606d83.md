[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/starknet_api/src/block.rs (L213-214)
```rust
    #[serde(skip_serializing)]
    pub state_diff_commitment: Option<StateDiffCommitment>,
```

**File:** crates/starknet_api/src/block.rs (L722-735)
```rust
pub fn verify_block_signature(
    sequencer_pub_key: &SequencerPublicKey,
    signature: &BlockSignature,
    state_diff_commitment: &GlobalRoot,
    block_hash: &BlockHash,
) -> Result<bool, BlockVerificationError> {
    let message_hash = Poseidon::hash_array(&[block_hash.0, state_diff_commitment.0]);
    verify_message_hash_signature(&message_hash, &signature.0, &sequencer_pub_key.0).map_err(
        |err| BlockVerificationError::BlockSignatureVerificationFailed {
            block_hash: *block_hash,
            error: err,
        },
    )
}
```

**File:** crates/starknet_api/src/block_test.rs (L32-50)
```rust
fn block_signature_verification() {
    // Values taken from Mainnet.
    let block_hash =
        BlockHash(felt!("0x7d5db04c5ca2aea828180dc441afb1580e3cee7547a3567ced3aa5bb8b273c0"));
    let state_commitment =
        GlobalRoot(felt!("0x64689c12248e1110af4b3af0e2b43cd51ad13e8855f10e37669e2a4baf919c6"));
    let signature = BlockSignature(Signature {
        r: felt!("0x1b382bbfd693011c9b7692bc932b23ed9c288deb27c8e75772e172abbe5950c"),
        s: felt!("0xbe4438085057e1a7c704a0da3b30f7b8340fe3d24c86772abfd24aa597e42"),
    });
    let sequencer_pub_key = SequencerPublicKey(PublicKey(felt!(
        "0x48253ff2c3bed7af18bde0b611b083b39445959102d4947c51c4db6aa4f4e58"
    )));

    assert!(
        verify_block_signature(&sequencer_pub_key, &signature, &state_commitment, &block_hash)
            .unwrap()
    );
}
```
