### Title
AuxPoW Validation Silently Bypassed When `aux_data` Is `None` in Dogecoin `submit_block_header` — (File: `contract/src/dogecoin.rs`)

### Summary
The Dogecoin `submit_block_header` function accepts `(Header, Option<AuxData>)`. When `aux_data` is `None`, the entire AuxPoW validation path is skipped and only a direct Scrypt PoW hash check is applied. This is the exact analog of M-02: a code branch that should be unreachable in production (all Dogecoin mainnet blocks after height 371,337 are AuxPoW) is reachable by any caller, causing a weaker, incorrect validation path to be silently substituted for the full AuxPoW proof check.

### Finding Description
In `contract/src/dogecoin.rs`, `submit_block_header` (lines 176–188):

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

When `aux_data` is `None`, `check_aux` is never called. The following invariants enforced inside `check_aux` are entirely skipped:

| Check | Location in `check_aux` |
|---|---|
| AuxPoW flag set in `version` (`& 0x100`) | line 54–61 |
| Block's chain ID matches configured `aux_chain_id` (0x0062) | lines 63–71 |
| Parent block's chain ID does NOT match | lines 73–76 |
| Chain merkle proof: Dogecoin block hash is in parent coinbase tree | lines 78–82 |
| Coinbase tx merkle proof matches `parent_block.merkle_root` | lines 87–93 |
| Merged-mining header position in coinbase script | lines 106–122 |
| `n_size` matches chain merkle proof length | lines 130–135 |
| `aux_data.chain_id` equals `get_expected_index(nonce, chain_id, proof_len)` | lines 142–148 |
| Parent block's Scrypt hash ≤ target | lines 149–154 |

The `else` branch applies only a direct Scrypt hash check on the Dogecoin block header itself. For any real Dogecoin block above height 371,337, the version has bit 8 set and the actual PoW lives in the parent Bitcoin block's header — the Dogecoin header's own Scrypt hash is not below the target. However, an attacker can craft a **synthetic** Dogecoin block header (version without bit 8, arbitrary `prev_block_hash` pointing to a known stored header, valid `bits` matching the expected difficulty) and mine a Scrypt preimage below the target. Submitting this with `aux_data = None` causes the contract to accept it as a valid mainchain block, bypassing all merged-mining proof requirements.

The `BlockHeader` type alias for Dogecoin is `(Header, Option<AuxData>)` — the caller fully controls whether `aux_data` is `Some` or `None`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
A synthetic Dogecoin block header with no AuxPoW data, a valid Scrypt hash, and a `prev_block_hash` pointing to any stored header can be accepted into `headers_pool` and promoted to the mainchain tip if its computed `chain_work` exceeds the current tip's. Once accepted:

- `mainchain_tip_blockhash` is corrupted to a non-canonical hash.
- `mainchain_height_to_header` and `mainchain_header_to_height` are updated with the fake block.
- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will use the fake block's `merkle_root` for proof verification, enabling false-positive transaction inclusion proofs for transactions that never existed on the real Dogecoin chain. [4](#0-3) [5](#0-4) 

### Likelihood Explanation
Medium-low. The attacker must be a registered trusted relayer (the `submit_blocks` entry point is gated by `#[trusted_relayer]`), which requires staking. However, the staking requirement is economic, not administrative — it does not require a privileged key. Once registered, the attacker must mine a Scrypt block at the current Dogecoin difficulty. On testnet (where `pow_allow_min_difficulty_blocks = true` and difficulty can drop to `proof_of_work_limit_bits = 0x1e0fffff`), this is trivially achievable with commodity hardware. On mainnet the Scrypt difficulty is high, but the attack remains structurally reachable. [6](#0-5) [7](#0-6) 

### Recommendation
1. In the `else` branch of `submit_block_header`, add an explicit check that the block version does **not** have the AuxPoW flag set (`block_header.version & 0x100 == 0`). If the flag is set but no `aux_data` is provided, panic.
2. Enforce that blocks above the AuxPoW activation height (371,337 for mainnet) must supply `aux_data = Some(...)`. Reject `None` unconditionally above that height.
3. Consider making `AuxData` non-optional in the `BlockHeader` type alias for Dogecoin, forcing callers to always supply it and eliminating the silent fallback path entirely. [8](#0-7) 

### Proof of Concept
1. Register as a trusted relayer on a Dogecoin testnet deployment.
2. Read the current `mainchain_tip_blockhash` and its `bits` field via `get_last_block_header()`.
3. Construct a `Header` with:
   - `version = 1` (no AuxPoW flag, no chain ID)
   - `prev_block_hash` = current tip hash
   - `bits` = value returned by `get_next_work_required` for the next block
   - `time` > MTP of the current tip
4. Mine a Scrypt nonce such that `scrypt(header_bytes)` < `target_from_bits(bits)`. On testnet this is near-instant.
5. Call `submit_blocks([(crafted_header, None)])` with sufficient attached deposit.
6. The contract calls `check_target` (passes — bits are correct), then enters the `else` branch, verifies the Scrypt hash (passes — mined correctly), and stores the block as a valid mainchain block.
7. `get_last_block_header()` now returns the synthetic block. Any subsequent `verify_transaction_inclusion` call against this block's hash will use the attacker-chosen `merkle_root`, returning `true` for any crafted Merkle proof. [9](#0-8) [10](#0-9)

### Citations

**File:** contract/src/dogecoin.rs (L23-47)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let expected_bits =
            get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );

        // Check timestamp against median time past of the previous 11 blocks
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Reject blocks whose timestamp is more than 2 hours ahead of local time
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
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

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** btc-types/src/network.rs (L94-106)
```rust
        Network::Testnet => DogecoinConfig {
            difficulty_adjustment_interval: 1,
            pow_target_timespan: 60,
            proof_of_work_limit_bits: 0x1e0fffff,
            pow_target_spacing: 60, // 1 minute
            pow_allow_min_difficulty_blocks: true,
            pow_limit: U256::new(
                0x0000_0fff_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
            aux_chain_id: 0x0062,
        },
    }
```
