### Title
Missing AuxPoW Version-Flag Check Allows Fraudulent Dogecoin Block Injection — (`contract/src/dogecoin.rs`)

### Summary

In the Dogecoin build of the BTC light client, `submit_block_header` routes validation based solely on whether `aux_data` is `Some` or `None`. When `aux_data` is `None`, the contract performs a direct SHA256d PoW check but **never verifies that the block's version field does not have the AuxPoW flag set**. A malicious trusted relayer can therefore submit a block whose version carries `BLOCK_VERSION_AUXPOW` (bit 8) while omitting `AuxData`, causing the contract to accept it through the weaker direct-hash path and inject a fraudulent block into the canonical chain.

### Finding Description

`check_aux` (the AuxPoW validation path) correctly enforces that when AuxData is present the block's version must have the AuxPoW flag set: [1](#0-0) 

The symmetric guard — rejecting a block whose version carries `BLOCK_VERSION_AUXPOW` when no `AuxData` is supplied — is absent in the non-AuxPoW branch of `submit_block_header`: [2](#0-1) 

The two co-set states are:
- **State A**: `block_header.version & BLOCK_VERSION_AUXPOW != 0` (AuxPoW flag in the version field)
- **State B**: `aux_data == None` (no AuxData supplied by the relayer)

The contract branches on State B but never checks State A when State B is true, exactly mirroring the original report's pattern where `offerStatus == Settled` was checked while `abortOfferStatus == Aborted` was ignored.

When `aux_data` is `None`, the PoW check performed is: [3](#0-2) 

`block_hash_pow()` compiles to SHA256d for the Dogecoin build because the `scrypt_hash` feature is not activated: [4](#0-3) 

Dogecoin's difficulty target is calibrated for Scrypt (N=1024, r=1, p=1). SHA256d is orders of magnitude faster, so finding a nonce that satisfies `SHA256d(header) ≤ target` requires far less work than legitimate Dogecoin mining.

### Impact Explanation

A fraudulent block accepted through this path is stored in `headers_pool` and promoted to the canonical chain via `store_block_header` / `submit_block_header_inner`: [5](#0-4) 

Once the fraudulent block is on the canonical chain, any downstream contract or user calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against that block height will receive a `true` result for a fabricated transaction: [6](#0-5) 

This corrupts the canonical chain state and enables fraudulent SPV proofs — the core security guarantee of the light client — to be accepted by any consumer of the contract.

### Likelihood Explanation

`submit_blocks` is gated by `#[trusted_relayer]`: [7](#0-6) 

The trusted-relayer system is staking-based, not a closed privileged role; any account that meets the staking requirement can become a trusted relayer. A malicious actor who stakes and becomes a trusted relayer can immediately exploit this path. The computational cost of the attack (SHA256d mining to Dogecoin's Scrypt-calibrated target) is low relative to the damage caused.

### Recommendation

Add a version-flag guard in the `else` branch of `submit_block_header` before the direct PoW check:

```rust
} else {
+   require!(
+       block_header.version & BLOCK_VERSION_AUXPOW == 0,
+       "Non-AuxPoW submission must not have AuxPoW flag set in version"
+   );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        format!("block should have correct pow")
    );
}
```

This mirrors the existing check inside `check_aux` and closes the routing gap.

### Proof of Concept

1. Attacker stakes to become a trusted relayer on the Dogecoin deployment.
2. Attacker crafts a `Header` with:
   - `version` having bit 8 set (`version | 0x100`)
   - `prev_block_hash` pointing to the current chain tip
   - `bits` equal to the current difficulty (passes `check_target`)
   - A `nonce` iterated until `SHA256d(header) ≤ target_from_bits(bits)` — feasible in seconds on commodity hardware because SHA256d is not Scrypt.
3. Attacker calls `submit_blocks([(crafted_header, None)])`.
4. `submit_block_header` takes the `else` branch, the SHA256d check passes, and the fraudulent block is stored as a canonical main-chain block.
5. Attacker constructs a fake Merkle proof for a non-existent transaction in that block and calls `verify_transaction_inclusion` — the function returns `true`, enabling fraudulent cross-chain settlement or bridge withdrawals by any contract consuming this result.

### Citations

**File:** contract/src/dogecoin.rs (L52-61)
```rust
        const BLOCK_VERSION_AUXPOW: i32 = 0x100;

        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );
```

**File:** contract/src/dogecoin.rs (L176-189)
```rust
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
```

**File:** btc-types/src/btc_header.rs (L38-53)
```rust
    pub fn block_hash_pow(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        #[cfg(feature = "scrypt_hash")]
        {
            let params = scrypt::Params::new(10, 1, 1, 32).unwrap(); // N=1024 (2^10), r=1, p=1

            let mut output = [0u8; 32];
            scrypt::scrypt(&block_header, &block_header, &params, &mut output).unwrap();
            H256::from(output)
        }

        #[cfg(not(feature = "scrypt_hash"))]
        {
            double_sha256(&block_header)
        }
    }
```

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
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
