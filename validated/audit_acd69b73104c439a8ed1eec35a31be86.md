### Title
Missing AuxPoW Version-Flag Restriction Check in `submit_block_header` Allows Standalone-PoW Bypass for AuxPoW-Flagged Blocks — (`contract/src/dogecoin.rs`)

---

### Summary

In the Dogecoin build of the BTC light client, `submit_block_header` accepts a block with the AuxPoW version flag set (`version & 0x100 != 0`) while `aux_data` is `None`. When no AuxPoW data is supplied, the function falls through to a standalone Scrypt PoW check instead of rejecting the submission. This is the direct analog of the `swapBorrowRateMode` bug: a state-transition function does not verify that the requested mode is permitted for the given input, allowing a caller to bypass the mode-specific validation path entirely.

---

### Finding Description

In `contract/src/dogecoin.rs`, `submit_block_header` branches on whether `aux_data` is `Some` or `None`: [1](#0-0) 

```rust
if !skip_pow_verification {
    self.check_target(&block_header, &prev_block_header);

    if let Some(ref aux_data) = aux_data {
        self.check_aux(&block_header, aux_data);
    } else {
        let pow_hash = block_header.block_hash_pow();
        require!(
            U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
    }
}
```

Inside `check_aux`, the contract enforces that if AuxPoW data is present, the block version **must** carry the AuxPoW flag: [2](#0-1) 

```rust
require!(
    block_header.version & BLOCK_VERSION_AUXPOW != 0,
    "Aux POW block does not have AuxPoW flag set in version"
);
```

The **inverse check is absent**: when `aux_data` is `None`, there is no `require!(block_header.version & BLOCK_VERSION_AUXPOW == 0, ...)`. A caller may therefore submit a block whose version has bit 8 set — signalling a merged-mined block — while omitting the AuxPoW payload. The contract then validates the block using only the block header's own Scrypt hash against the target, completely skipping the coinbase-chain-merkle, chain-ID, nonce-index, and parent-block PoW checks that `check_aux` enforces. [3](#0-2) 

The `BlockHeader` type for the Dogecoin build is `(Header, Option<AuxData>)`, so any unprivileged NEAR caller invoking `submit_blocks` can supply `(header_with_auxpow_flag, None)`: [4](#0-3) 

---

### Impact Explanation

After Dogecoin block 371337, every valid on-chain block carries the AuxPoW flag and must include a valid AuxPoW payload. A block with the AuxPoW flag set but no AuxPoW data is **unconditionally invalid** on the Dogecoin network. By exploiting this missing check, an attacker who can produce a Scrypt hash meeting the current target can inject such a block into the light client's `headers_pool` and, if its chain work exceeds the current tip, trigger a chain reorganisation (`reorg_chain`) that installs a fabricated, network-invalid block as the canonical tip. [5](#0-4) 

Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against this corrupted tip will receive results anchored to a block that does not exist on the real Dogecoin chain, enabling proof-forgery for any transaction the attacker chooses to fabricate. [6](#0-5) 

---

### Likelihood Explanation

The attacker must produce a Scrypt hash meeting the live Dogecoin difficulty — equivalent to mining a real block. This is a high-cost precondition, but it is the **only** precondition: no privileged role, no leaked key, and no social engineering is required. The entry point is the public, payable `submit_blocks` call available to any NEAR account. [7](#0-6) 

The critical distinction from simply submitting a valid Dogecoin block is that the injected block is **invalid on the real network**, so it can never be part of the honest chain. Any light-client state built on top of it is permanently diverged from the actual Dogecoin ledger.

---

### Recommendation

Add an explicit guard in the `else` branch of `submit_block_header` to reject any block whose version carries the AuxPoW flag when no AuxPoW data is provided:

```rust
} else {
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "AuxPoW flag set in version but no AuxPoW data provided"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
}
``` [1](#0-0) 

Long term, add a property-based test (e.g., with `proptest`) asserting that any header with `version & 0x100 != 0` submitted without AuxPoW data is always rejected, mirroring the Echidna/Manticore recommendation in the original report.

---

### Proof of Concept

1. Attacker mines a Dogecoin block header with `version = 0x00620104` (AuxPoW flag bit 8 set, chain ID = 98 for Dogecoin mainnet) whose Scrypt hash satisfies the current target.
2. Attacker calls `submit_blocks` on the NEAR contract with the payload `[(header, None)]` — i.e., the AuxPoW-flagged header and no `AuxData`.
3. `submit_block_header` calls `check_target` (passes — Scrypt hash meets target), then enters the `else` branch (no `aux_data`), and calls only the standalone `require!(pow_hash <= target)` check (passes — same hash).
4. `check_aux` is never invoked; chain-ID, coinbase-merkle, parent-block PoW, and nonce-index checks are all skipped.
5. The block is stored via `submit_block_header_inner`. If its chain work exceeds the current tip, `reorg_chain` promotes it to the canonical chain.
6. Subsequent `verify_transaction_inclusion_v2` calls against this tip return results for transactions that do not exist on the real Dogecoin network. [8](#0-7)

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

**File:** btc-types/src/header.rs (L19-20)
```rust
#[cfg(feature = "dogecoin_header")]
pub type BlockHeader = (Header, Option<AuxData>);
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

**File:** contract/src/lib.rs (L347-368)
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
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```
