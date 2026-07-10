### Title
Missing AuxPoW Flag Enforcement in Non-AuxPoW Dogecoin Block Submission Path - (File: contract/src/dogecoin.rs)

### Summary
The Dogecoin `submit_block_header` function has two distinct validation code paths: one for blocks submitted with AuxPoW data (`aux_data = Some(...)`) and one for blocks submitted without it (`aux_data = None`). The non-AuxPoW path omits the check that the block's version field does **not** have the AuxPoW flag set (`BLOCK_VERSION_AUXPOW = 0x100`). This allows a block that claims to be an AuxPoW block (by having the flag set in its version) to be accepted by the contract without providing any AuxPoW data, bypassing the chain ID validation that is mandatory in the AuxPoW path.

### Finding Description
In `contract/src/dogecoin.rs`, `submit_block_header` branches on whether `aux_data` is `Some` or `None`: [1](#0-0) 

When `aux_data` is `None`, the only PoW check performed is that the block's own hash meets the declared target. No check is made that `block_header.version & BLOCK_VERSION_AUXPOW == 0`.

By contrast, the `check_aux` path enforces three additional invariants that are completely absent from the `None` branch: [2](#0-1) 

Specifically:
1. The AuxPoW flag **must** be set in the version (`BLOCK_VERSION_AUXPOW != 0`).
2. The block's chain ID (upper 16 bits of `version`) **must** equal the Dogecoin chain ID (`0x0062`).
3. The parent block's chain ID **must not** equal the Dogecoin chain ID.

When `aux_data = None`, all three checks are skipped. A block with the AuxPoW flag set and an arbitrary chain ID in its version field is accepted as a valid Dogecoin block, provided its own hash meets the target.

The `BlockHeader` type for the Dogecoin build is `(Header, Option<AuxData>)`: [3](#0-2) 

Any caller of `submit_blocks` can supply `(header, None)` with the AuxPoW flag set in `header.version`. [4](#0-3) 

### Impact Explanation
The Dogecoin network rejects blocks that have the AuxPoW flag set in their version but carry no AuxPoW data. If such a block is accepted by the NEAR contract, the contract's canonical chain diverges from the real Dogecoin chain. Once the contract's mainchain tip is a block that the real Dogecoin network considers invalid, all subsequent `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` calls that reference blocks built on top of that tip return results against a forged chain state. SPV proofs verified against the contract would be meaningless, as the Merkle roots stored in the contract's headers pool would not correspond to any real Dogecoin block.

The chain ID bypass is the most direct broken invariant: the contract is supposed to track only Dogecoin blocks (chain ID `0x0062`), but a block with any chain ID in its version field is accepted when `aux_data = None`. [5](#0-4) 

### Likelihood Explanation
Exploiting this requires the attacker to mine a Dogecoin block whose own double-SHA256 hash meets the current Dogecoin target, with the AuxPoW flag set in the version field. This is computationally equivalent to mining a legitimate Dogecoin block. The attacker must therefore be a miner (or pool) with non-trivial hash rate. The likelihood is low for an opportunistic attacker but non-negligible for a motivated miner, particularly during periods of low Dogecoin network difficulty.

### Recommendation
Add an explicit check in the `None` branch of `submit_block_header` to reject any block whose version has the AuxPoW flag set:

```rust
} else {
    // Reject blocks that claim to be AuxPoW blocks but provide no AuxPoW data
    const BLOCK_VERSION_AUXPOW: i32 = 0x100;
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "Non-AuxPoW submission must not have AuxPoW flag set in version"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        format!("block should have correct pow")
    );
}
```

This mirrors the existing check in `check_aux` and closes the gap between the two validation paths. [6](#0-5) 

### Proof of Concept
1. Build the contract with `feature = "dogecoin"`.
2. Initialize the contract with a valid Dogecoin genesis block.
3. Construct a `Header` where `version = 0x00620100` (chain ID `0x0062`, AuxPoW flag `0x0100` set) and mine it until `double_sha256(header_bytes) <= target_from_bits(header.bits)`.
4. Call `submit_blocks([(header, None)])` on the NEAR contract.
5. The contract accepts the block and stores it in `headers_pool` and `mainchain_height_to_header`, even though the real Dogecoin network would reject it for having the AuxPoW flag set without AuxPoW data.
6. The contract's `mainchain_tip_blockhash` now points to a block that does not exist on the real Dogecoin chain. [7](#0-6)

### Citations

**File:** contract/src/dogecoin.rs (L49-76)
```rust
    pub(crate) fn check_aux(&mut self, block_header: &Header, aux_data: &AuxData) {
        // The Dogecoin block must have the AuxPoW flag set (bit 8) when AuxPoW data is present.
        // https://github.com/dogecoin/dogecoin/blob/master/src/auxpow.h
        const BLOCK_VERSION_AUXPOW: i32 = 0x100;

        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );

        let chain_id = self.get_config().aux_chain_id;

        require!(
            chain_id == block_header.get_chain_id(),
            format!(
                "block does not have our chain ID (got {}, expected {chain_id})",
                block_header.get_chain_id()
            )
        );

        require!(
            chain_id != aux_data.parent_block.get_chain_id(),
            "Aux POW parent has our chain ID"
        );
```

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
    }
```

**File:** btc-types/src/header.rs (L19-20)
```rust
#[cfg(feature = "dogecoin_header")]
pub type BlockHeader = (Header, Option<AuxData>);
```

**File:** contract/src/lib.rs (L169-179)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```
