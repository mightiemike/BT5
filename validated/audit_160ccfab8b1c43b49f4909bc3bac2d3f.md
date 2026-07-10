### Title
AuxPoW Parent Block PoW Validated Against Dogecoin Target Instead of Parent Block's Own Target - (`contract/src/dogecoin.rs`)

### Summary
In `check_aux`, the parent block's proof-of-work hash is validated against the **Dogecoin block's** difficulty target (`block_header.bits`) rather than the **parent block's own** difficulty target (`aux_data.parent_block.bits`). Because Dogecoin's difficulty is orders of magnitude easier than Bitcoin's, an attacker can forge AuxPoW using only Dogecoin-level mining power, bypassing the security guarantee that merged mining provides.

### Finding Description

In `contract/src/dogecoin.rs`, `check_aux` performs the final PoW check on the AuxPoW parent block:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    format!("block should have correct pow")
);
``` [1](#0-0) 

`block_header` is the Dogecoin block being submitted. `block_header.bits` encodes Dogecoin's current difficulty target. `aux_data.parent_block` is the Bitcoin (parent chain) block header, which carries its own `bits` field encoding Bitcoin's current difficulty target. [2](#0-1) 

The Dogecoin reference implementation (`auxpow.cpp`) checks the parent block's hash against the **parent block's own `nBits`**:

```cpp
if (!CheckProofOfWork(parentBlock.GetHash(), parentBlock.nBits, params))
    return error("%s : aux proof of work failed", __func__);
```

The contract instead checks against `block_header.bits` (Dogecoin's target). These are two completely different difficulty parameters from two different chains. The `Header` struct used for both the Dogecoin block and the parent block is the same type, each carrying its own independent `bits` field. [3](#0-2) 

### Impact Explanation

Bitcoin's difficulty is typically many orders of magnitude harder than Dogecoin's (Bitcoin's target is a much smaller number). By checking the parent block's hash against Dogecoin's easy target instead of Bitcoin's hard target, the contract allows an attacker to:

1. Construct a fake Bitcoin-format parent block header whose hash meets Dogecoin's easy difficulty but **not** Bitcoin's actual difficulty.
2. Embed a valid coinbase transaction committing to the attacker's Dogecoin block hash.
3. Construct valid chain and coinbase merkle proofs.
4. Submit the resulting `(Header, Some(AuxData))` tuple via `submit_blocks()`.

The contract's `check_aux` will accept this as valid AuxPoW. The attacker can inject arbitrary Dogecoin block headers into the light client's chain state, corrupt the canonical chain, and cause `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to return `true` for transactions that were never confirmed on the real Dogecoin chain. [4](#0-3) 

### Likelihood Explanation

The `submit_blocks` entrypoint is callable by any trusted relayer (or, with the `UnrestrictedSubmitBlocks` role bypass, by any account). The attacker only needs Dogecoin-level mining power — far below Bitcoin-level — to produce a parent block hash satisfying the incorrectly applied target. The construction of a valid coinbase commitment and merkle proofs requires no special privileges. This is a realistic, low-cost attack path. [5](#0-4) 

### Recommendation

Replace `block_header.bits` with `aux_data.parent_block.bits` in the PoW check inside `check_aux`:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(aux_data.parent_block.bits),
    format!("block should have correct pow")
);
```

Additionally, validate that `aux_data.parent_block.bits` encodes a target no easier than the Dogecoin block's target (i.e., `target_from_bits(aux_data.parent_block.bits) <= target_from_bits(block_header.bits)`), mirroring the Dogecoin reference implementation's `CheckTarget` call, to prevent an attacker from supplying an artificially inflated `parent_block.bits`.

### Proof of Concept

1. Obtain the current Dogecoin mainchain tip and its `bits` value (e.g., `0x1b0404cb`, target ≈ 2^220).
2. Construct a Bitcoin-format `Header` (`parent_block`) with arbitrary fields. Mine its `nonce` until `double_sha256(parent_block_bytes) < target_from_bits(dogecoin_bits)`. This requires only ~2^(256-220) = 2^36 hashes — trivially achievable.
3. Build a coinbase transaction whose `script_sig` contains the merged-mining header `fabe6d6d` followed by the Dogecoin block's hash, plus valid size/nonce bytes.
4. Compute `merkle_proof` so that the coinbase txid is the `parent_block.merkle_root`.
5. Compute `chain_merkle_proof` (empty, `chain_id = 0`) so that `compute_root_from_merkle_proof(dogecoin_block_hash, 0, []) == chain_root` embedded in the coinbase.
6. Submit `(dogecoin_header, Some(AuxData { coinbase_tx, merkle_proof, chain_merkle_proof, chain_id: 0, parent_block }))` to `submit_blocks()`.
7. `check_aux` passes because `pow_hash <= target_from_bits(block_header.bits)` (Dogecoin's easy target), even though `pow_hash > target_from_bits(parent_block.bits)` (Bitcoin's hard target). The forged block is accepted into the light client's chain state. [6](#0-5)

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

**File:** btc-types/src/aux.rs (L10-16)
```rust
pub struct AuxData {
    pub coinbase_tx: Vec<u8>,
    pub merkle_proof: Vec<H256>,
    pub chain_merkle_proof: Vec<H256>,
    pub chain_id: usize,
    pub parent_block: Header,
}
```

**File:** btc-types/src/btc_header.rs (L10-23)
```rust
pub struct Header {
    /// Block version, now repurposed for soft fork signalling.
    pub version: i32,
    /// Reference to the previous block in the chain.
    pub prev_block_hash: H256,
    /// The root hash of the merkle tree of transactions in the block.
    pub merkle_root: H256,
    /// The timestamp of the block, as claimed by the miner.
    pub time: u32,
    /// The target value below which the blockhash must lie.
    pub bits: u32,
    /// The nonce, selected to obtain a low enough blockhash.
    pub nonce: u32,
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
