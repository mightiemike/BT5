### Title
Missing AuxPoW-flag guard when `AuxData` is `None` allows bypassing all AuxPoW-specific validation — (`contract/src/dogecoin.rs`)

### Summary
In the Dogecoin build, `submit_block_header` accepts `(Header, Option<AuxData>)`. When the caller supplies `aux_data = None`, the function falls back to a direct double-SHA256 PoW check on the Dogecoin block header. It never verifies that the block version does **not** have the AuxPoW flag set (`version & 0x100 == 0`). A caller can therefore submit a block whose version signals AuxPoW but whose `AuxData` is absent, silently skipping every AuxPoW-specific security check.

### Finding Description

`BlockHeader` for the Dogecoin build is defined as `(Header, Option<AuxData>)`: [1](#0-0) 

`submit_blocks` accepts a caller-supplied `Vec<BlockHeader>` and iterates over them: [2](#0-1) 

Inside `submit_block_header`, when `aux_data` is `None` the code falls through to a plain PoW check with no guard on the version field: [3](#0-2) 

By contrast, `check_aux` — which is only reached when `aux_data` is `Some` — explicitly requires the AuxPoW flag to be set: [4](#0-3) 

The inverse guard (reject a block whose version has `BLOCK_VERSION_AUXPOW` set when no `AuxData` is provided) is entirely absent. The full set of checks that are bypassed when `aux_data = None` for an AuxPoW-flagged block:

| Check in `check_aux` | Bypassed? |
|---|---|
| Chain merkle proof length ≤ 30 | Yes |
| Block version has AuxPoW flag | Yes (ironic: checked only when data is present) |
| Block chain ID matches Dogecoin chain ID (0x0062) | Yes |
| Parent block does not have Dogecoin chain ID | Yes |
| Chain merkle root computed from Dogecoin block hash | Yes |
| Coinbase tx contains chain merkle root | Yes |
| Merged-mining header position in coinbase | Yes |
| `n_size` matches `1 << chain_merkle_proof.len()` | Yes |
| `chain_id` index matches expected index from nonce | Yes |
| Parent block PoW hash ≤ target | Yes | [5](#0-4) 

### Impact Explanation
An attacker who can call `submit_blocks` can craft a `(Header, None)` tuple where `header.version & 0x100 != 0` and the direct double-SHA256 of the 80-byte header is below the encoded target. The contract stores this as a valid Dogecoin block even though:

- It does not correspond to any real Dogecoin block (real post-activation blocks use AuxPoW and their direct hash is not required to be below target).
- The chain ID, coinbase, and parent-block PoW checks — the core of AuxPoW security — are entirely skipped.

A successful injection corrupts the `headers_pool` and `mainchain` maps, which are the authoritative source for `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`. A fake block at a chosen height can be used to make the contract falsely confirm a fabricated SPV proof. [6](#0-5) 

### Likelihood Explanation
The attacker must (a) hold a trusted-relayer stake and (b) mine a block whose direct double-SHA256 is below the current Dogecoin target — the same computational cost as honest mining. Likelihood is therefore **low**, but the entry path (`submit_blocks`) is a documented production interface, not a privileged admin function, and the `trusted_relayer` staking mechanism is open to any NEAR account that meets the economic threshold. [7](#0-6) 

### Recommendation
Add an explicit guard in the `None` branch of `aux_data` inside `submit_block_header`:

```rust
} else {
    // Reject blocks that signal AuxPoW but provide no AuxData
    const BLOCK_VERSION_AUXPOW: i32 = 0x100;
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "Block has AuxPoW flag set but no AuxData was provided"
    );

    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
}
```

This mirrors the pattern in `check_aux` (which already enforces the flag must be set when data is present) and closes the symmetric gap. [8](#0-7) 

### Proof of Concept
A registered relayer constructs and submits the following to `submit_blocks` on the Dogecoin deployment:

```rust
// Craft a header with the AuxPoW flag set in version
let fake_header = Header {
    version: 0x0062_0100_i32,  // chain_id=0x0062, AuxPoW flag=0x100
    prev_block_hash: <known tip hash>,
    merkle_root: H256::default(),
    time: <valid timestamp>,
    bits: <current target bits>,
    nonce: <mined nonce such that double_sha256(header) <= target>,
};

// Submit with None AuxData — bypasses all check_aux logic
submit_blocks(vec![(fake_header, None)]);
```

The contract accepts the block, stores it in `headers_pool`, and may promote it to the mainchain if its chainwork exceeds the current tip. A subsequent `verify_transaction_inclusion` call against this fake block height will return `true` for any fabricated `tx_id` paired with a matching Merkle proof constructed over the fake `merkle_root`. [9](#0-8)

### Citations

**File:** btc-types/src/header.rs (L19-20)
```rust
#[cfg(feature = "dogecoin_header")]
pub type BlockHeader = (Header, Option<AuxData>);
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/dogecoin.rs (L49-155)
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

        let chain_root = merkle_tools::compute_root_from_merkle_proof(
            block_header.block_hash(),
            aux_data.chain_id,
            &aux_data.chain_merkle_proof,
        );

        let coinbase_tx = aux_data.get_coinbase_tx();
        let coinbase_tx_hash = coinbase_tx.compute_txid();

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
                0,
                &aux_data.merkle_proof,
            ) == aux_data.parent_block.merkle_root
        );

        let script_sig = coinbase_tx
            .input
            .first()
            .unwrap()
            .script_sig
            .to_hex_string();
        let pos_merged_mining_header = script_sig.find(MERGED_MINING_HEADER);
        let mut pos_chain_root = script_sig
            .find(&chain_root.to_string())
            .expect("Aux POW missing chain merkle root in parent coinbase");

        match pos_merged_mining_header {
            Some(pos_merged_mining_header) => {
                if script_sig[pos_merged_mining_header + MERGED_MINING_HEADER.len()..]
                    .contains(MERGED_MINING_HEADER)
                {
                    env::panic_str("Multiple merged mining headers in coinbase");
                }

                require!(
                    pos_merged_mining_header + MERGED_MINING_HEADER.len() == pos_chain_root,
                    "Merged mining header is not just before chain merkle root"
                );
            }
            None => {
                require!(pos_chain_root <= 40, "Aux POW chain merkle root must start in the first 20 bytes of the parent coinbase");
            }
        }

        pos_chain_root += chain_root.to_string().len();
        require!(
            script_sig.len() - pos_chain_root >= 16,
            "Aux POW missing chain merkle tree size and nonce in parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root..pos_chain_root + 8]).unwrap();
        let n_size = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        require!(
            n_size == (1u32 << aux_data.chain_merkle_proof.len()),
            "Aux POW merkle branch size does not match parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root + 8..pos_chain_root + 16]).unwrap();
        let n_nonce = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);

        let chain_id = block_header.get_chain_id();

        let expected_index =
            Self::get_expected_index(n_nonce, chain_id, aux_data.chain_merkle_proof.len());

        require!(
            u32::try_from(aux_data.chain_id).ok() == Some(expected_index),
            "Aux POW wrong index"
        );
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
    }
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
