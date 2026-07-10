### Title
Missing AuxPoW Version-Flag Consistency Check Allows AuxPoW Validation Bypass - (File: contract/src/dogecoin.rs)

### Summary
In the Dogecoin build of the BTC light client, `submit_block_header` branches on whether `aux_data` is `Some` or `None` to decide which PoW path to execute. The `check_aux` path correctly requires the block's version to carry the AuxPoW flag (`version & 0x100 != 0`). The non-AuxPoW path, however, never checks that the block's version does **not** carry that flag. A relayer can therefore submit a block whose version advertises AuxPoW but whose `aux_data` field is `None`, causing the contract to skip every AuxPoW-specific check and accept the block on the strength of the block's own hash alone.

### Finding Description
`submit_block_header` in `contract/src/dogecoin.rs` dispatches on the presence of `aux_data`:

```rust
if let Some(ref aux_data) = aux_data {
    self.check_aux(&block_header, aux_data);   // validates AuxPoW fully
} else {
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
    // ← no check that version & BLOCK_VERSION_AUXPOW == 0
}
``` [1](#0-0) 

`check_aux` explicitly enforces the flag in the positive direction:

```rust
require!(
    block_header.version & BLOCK_VERSION_AUXPOW != 0,
    "Aux POW block does not have AuxPoW flag set in version"
);
``` [2](#0-1) 

There is no symmetric guard in the `else` branch requiring `block_header.version & BLOCK_VERSION_AUXPOW == 0`. The checks that are entirely skipped when `aux_data` is `None` include: chain-ID validation, parent-chain-ID exclusion, chain-merkle-proof verification, coinbase-merkle-proof verification, merged-mining-header position check, tree-size/nonce consistency, expected-index check, and parent-block PoW verification. [3](#0-2) 

The public entry point is `submit_blocks`, which accepts `Vec<BlockHeader>` where `BlockHeader = (Header, Option<AuxData>)` for the Dogecoin build. A caller supplies `(header_with_auxpow_flag, None)` to trigger the vulnerable branch. [4](#0-3) 

### Impact Explanation
A block accepted through this path is not a valid Dogecoin block: the real Dogecoin network rejects any block whose version carries the AuxPoW flag but that lacks a valid AuxPoW structure. The contract's canonical chain can therefore diverge from the real Dogecoin chain. Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a height occupied by such a fraudulent block will receive a result anchored to a header that does not exist on the real chain, enabling proof forgery for transactions that were never confirmed on Dogecoin. [5](#0-4) 

### Likelihood Explanation
The attacker must be a trusted relayer (or hold `Role::UnrestrictedSubmitBlocks`) to call `submit_blocks`. If the trusted-relayer staking mechanism is permissionless (stake-to-join), the barrier is economic rather than administrative. Beyond access, the attacker must produce a block whose own double-SHA256 hash meets the current Dogecoin difficulty target — real PoW work. This makes opportunistic exploitation expensive, but a well-resourced adversary (e.g., one already operating mining hardware) could craft such a block and submit it without AuxPoW data to plant a fraudulent header at a chosen height.

### Recommendation
Add an explicit version-flag consistency check in the `else` branch of `submit_block_header`:

```rust
} else {
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "Non-AuxPoW submission must not have AuxPoW flag set in version"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
}
```

This mirrors the existing guard inside `check_aux` and closes the asymmetry between the two paths. [6](#0-5) 

### Proof of Concept

1. Obtain trusted-relayer access (stake or hold `Role::UnrestrictedSubmitBlocks`).
2. Construct a `Header` whose `version` has bit 8 set (`version | 0x100`) and whose `prev_block_hash` points to a known block in the contract's pool.
3. Mine the header until `double_sha256(header_bytes) <= target_from_bits(bits)` (standard PoW, no AuxPoW parent required).
4. Call `submit_blocks` with `vec![(crafted_header, None)]`.
5. The contract executes the `else` branch, passes the PoW check, and stores the header in `headers_pool` and `mainchain_height_to_header`.
6. Call `verify_transaction_inclusion` with a fabricated `tx_id` and a crafted Merkle proof that hashes to the `merkle_root` embedded in the fraudulent header — the function returns `true` for a transaction that never existed on the real Dogecoin chain. [7](#0-6) [5](#0-4)

### Citations

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

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
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

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```
