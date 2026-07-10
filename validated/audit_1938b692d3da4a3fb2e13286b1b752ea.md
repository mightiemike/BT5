### Title
Dogecoin AuxPoW Flag Bypass: Block with AuxPoW Version Bit Set Accepted Without AuxPoW Verification — (File: contract/src/dogecoin.rs)

---

### Summary

In the Dogecoin variant of `submit_block_header`, when `aux_data` is `None`, the contract does not assert that the submitted block's version field does **not** carry the AuxPoW flag (`version & 0x100 != 0`). A relayer-path caller can therefore submit a Dogecoin block header that has the AuxPoW version bit set while omitting all AuxPoW data. The entire `check_aux` verification path is silently bypassed; the contract falls back to checking only the direct Scrypt hash of the Dogecoin header and, if that hash satisfies the target, stores the block as canonical. This is the direct structural analog of the BeaconKit bug: instead of wrapping errors as non-fatal so that `createProcessProposalResponse` always returns ACCEPT, this code silently substitutes a weaker verification path when the critical data is absent, producing the same outcome — an invalid block is accepted.

---

### Finding Description

`submit_block_header` in `contract/src/dogecoin.rs` (lines 166–204) destructures the caller-supplied tuple `(Header, Option<AuxData>)` and branches on whether AuxPoW data was provided:

```rust
if !skip_pow_verification {
    self.check_target(&block_header, &prev_block_header);   // bits + timestamp only

    if let Some(ref aux_data) = aux_data {
        self.check_aux(&block_header, aux_data);            // full AuxPoW chain check
    } else {
        let pow_hash = block_header.block_hash_pow();
        require!(                                           // direct Scrypt hash only
            U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
    }
}
``` [1](#0-0) 

`check_aux` (lines 49–155) enforces the complete AuxPoW protocol: the AuxPoW version flag must be set, the chain ID must match, the parent block must not carry the Dogecoin chain ID, the Dogecoin block hash must appear in the parent coinbase via a valid chain-merkle proof, the merged-mining header must be positioned correctly, the chain-merkle-tree size and nonce-derived index must be consistent, and the parent block's own PoW hash must satisfy the Dogecoin difficulty target. [2](#0-1) 

None of that is executed when `aux_data = None`. Crucially, there is **no guard** in the `else` branch that rejects a header whose `version & 0x100 != 0`. The constant `BLOCK_VERSION_AUXPOW = 0x100` is defined inside `check_aux` and is never consulted in the `else` path. [3](#0-2) 

In the Dogecoin consensus rules, a block with the AuxPoW version bit set is only valid when accompanied by a complete AuxPoW structure; full nodes reject it otherwise. The light client's `else` branch makes no such distinction: it accepts the block as long as the header's own Scrypt hash is below the target, regardless of what the version field declares.

The public entry point is `submit_blocks` in `contract/src/lib.rs` (lines 169–198), which is gated by `#[trusted_relayer]` and iterates over the caller-supplied `Vec<BlockHeader>` (which for the Dogecoin build is `Vec<(Header, Option<AuxData>)>`), passing each element directly to `submit_block_header`. [4](#0-3) 

A relayer supplies the `Option<AuxData>` field; passing `None` for a header whose version carries the AuxPoW bit is the attacker-controlled trigger.

---

### Impact Explanation

If the attack succeeds, the light client stores a block in `headers_pool` and potentially promotes it to `mainchain_tip_blockhash` via `submit_block_header_inner`. [5](#0-4) 

This block is invalid under Dogecoin consensus (full nodes reject it), so the light client's canonical chain diverges from the real Dogecoin network. Any NEAR contract or user calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against the corrupted chain receives a false SPV proof result — the core security guarantee of the light client is broken. [6](#0-5) [7](#0-6) 

The corrupted canonical mapping (`mainchain_height_to_header`, `mainchain_header_to_height`) persists in contract storage and affects all subsequent callers until a chain reorganization overwrites it.

---

### Likelihood Explanation

The attacker must (1) hold a trusted-relayer role and (2) produce a Dogecoin header with `version & 0x100 != 0` whose direct Scrypt hash satisfies the current difficulty target. Requirement (2) demands the same proof-of-work as mining a legitimate Dogecoin block. On mainnet this is expensive; on testnet (where `Network::Testnet` is supported and difficulty is low) it is feasible with modest hardware. The audit scope explicitly includes "relayer-path user supplying adversarial chain data," so the trusted-relayer entry point is in scope. Likelihood is **low on mainnet, moderate on testnet**.

---

### Recommendation

Add an explicit rejection in the `else` branch of `submit_block_header` for any header that declares the AuxPoW version bit but provides no AuxPoW data:

```rust
} else {
    const BLOCK_VERSION_AUXPOW: i32 = 0x100;
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "AuxPoW flag set in version but no AuxPoW data provided"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        format!("block should have correct pow")
    );
}
```

This mirrors the fix applied to BeaconKit (wrapping errors as fatal so that `createProcessProposalResponse` returns REJECT): the missing AuxPoW data is treated as a fatal protocol violation rather than a silent fallback.

---

### Proof of Concept

1. Deploy the Dogecoin build of the contract (`make build-dogecoin`).
2. Register a trusted-relayer account.
3. Construct a `Header` with `version = 0x00000101` (version 1 with AuxPoW bit set) and valid `bits` matching the current expected difficulty.
4. Mine (or on testnet, trivially find) a nonce such that the Scrypt hash of this header satisfies `hash <= target_from_bits(bits)`.
5. Call `submit_blocks` with `[(header, None)]` — `aux_data` is `None`.
6. Observe: `check_aux` is never called; the block passes the `else` branch and is stored in `headers_pool`.
7. Call `verify_transaction_inclusion` with a fabricated merkle proof against the now-canonical invalid block; the function returns `true` for a transaction that does not exist on the real Dogecoin chain. [8](#0-7)

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
