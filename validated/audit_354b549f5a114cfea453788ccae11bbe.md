### Title
Wrong Hash Function Applied to AuxPoW Parent Block PoW Check Allows Forged Dogecoin Block Injection — (File: `contract/src/dogecoin.rs`, `btc-types/src/btc_header.rs`)

---

### Summary

When the Dogecoin build is compiled with the `scrypt_hash` feature (required for non-AuxPoW Dogecoin block verification), the same `block_hash_pow()` function is used to hash both Dogecoin blocks (correctly Scrypt) and Bitcoin parent blocks in AuxPoW (incorrectly Scrypt instead of SHA256d). An unprivileged NEAR caller can exploit this by submitting a crafted AuxPoW block whose fake Bitcoin parent block has a low Scrypt hash, satisfying the PoW check without performing any real Bitcoin mining work.

---

### Finding Description

`block_hash_pow()` in `btc-types/src/btc_header.rs` is a single function that dispatches on the compile-time `scrypt_hash` feature flag:

```rust
pub fn block_hash_pow(&self) -> H256 {
    let block_header = self.get_block_header_vec();
    #[cfg(feature = "scrypt_hash")]
    {
        let params = scrypt::Params::new(10, 1, 1, 32).unwrap();
        let mut output = [0u8; 32];
        scrypt::scrypt(&block_header, &block_header, &params, &mut output).unwrap();
        H256::from(output)
    }
    #[cfg(not(feature = "scrypt_hash"))]
    {
        double_sha256(&block_header)
    }
}
``` [1](#0-0) 

For the Dogecoin build, `scrypt_hash` must be enabled so that non-AuxPoW Dogecoin blocks (pre-block 371337) are hashed with Scrypt. However, in `check_aux`, the same function is called on `aux_data.parent_block`, which is a **Bitcoin** block:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    format!("block should have correct pow")
);
``` [2](#0-1) 

Bitcoin blocks are mined with SHA256d, not Scrypt. The Dogecoin AuxPoW protocol requires that the parent block's **SHA256d** hash be below the Dogecoin target — not its Scrypt hash. The contract instead computes and checks the Scrypt hash of the Bitcoin parent block, which is the wrong cryptographic primitive for this security-critical check.

The `parent_block` field is of type `Header` (from `btc-types/src/btc_header.rs`), the same struct used for Dogecoin blocks, with no type-level distinction between a Dogecoin block and a Bitcoin parent block. [3](#0-2) 

---

### Impact Explanation

An attacker can submit a crafted Dogecoin AuxPoW block via `submit_blocks()` where:

1. The Dogecoin block header is attacker-controlled.
2. The coinbase transaction embeds the Dogecoin block hash at the correct position in the script.
3. The `parent_block` is a fake Bitcoin-format header whose **Scrypt** hash (not SHA256d) is below the Dogecoin target.
4. The merkle proofs for the coinbase and chain are constructed to match.

Because the contract checks Scrypt instead of SHA256d for the parent block, the attacker does not need to perform any real Bitcoin mining. Finding a 80-byte block header whose Scrypt hash is below the Dogecoin target is computationally feasible and far cheaper than finding one whose SHA256d hash is below the same target.

A successfully injected block is stored in `headers_pool` and can become the mainchain tip if its chainwork exceeds the current tip. This corrupts `mainchain_tip_blockhash` and the `mainchain_height_to_header` / `mainchain_header_to_height` maps, enabling false positive results from `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` for any consumer contract relying on the light client. [4](#0-3) 

---

### Likelihood Explanation

The entry point `submit_blocks()` is reachable by any trusted relayer (or any caller if the trusted-relayer restriction is bypassed via `UnrestrictedSubmitBlocks` role, which is a separate concern). The `scrypt_hash` feature is necessarily enabled for the Dogecoin build because non-AuxPoW Dogecoin blocks require it. The construction of a valid AuxPoW structure with a fake parent block is a known technique requiring only moderate engineering effort — no privileged access, no leaked keys, and no social engineering are needed.

---

### Recommendation

Separate the PoW hash computation for the AuxPoW parent block from the Dogecoin block's own hash. In `check_aux`, replace:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
```

with a direct call to `double_sha256` (SHA256d), since the parent block in Dogecoin AuxPoW is always a Bitcoin block:

```rust
let pow_hash = aux_data.parent_block.block_hash(); // SHA256d, not Scrypt
```

Alternatively, add a dedicated `parent_block_hash_pow()` method to `Header` that always uses SHA256d regardless of the active feature flag, and use it exclusively in the AuxPoW verification path. [5](#0-4) 

---

### Proof of Concept

1. Construct a Dogecoin block header `doge_hdr` with `version | 0x100` (AuxPoW flag set) and `get_chain_id() == 0x0062`.
2. Construct a fake Bitcoin-format `parent_block` header. Iterate the `nonce` field until `scrypt(parent_block_bytes, parent_block_bytes, N=1024, r=1, p=1)` is numerically less than `target_from_bits(doge_hdr.bits)`. This requires only Scrypt work, not SHA256d work.
3. Build a coinbase transaction whose `script_sig` contains `fabe6d6d` followed immediately by `doge_hdr.block_hash()` (SHA256d of the Dogecoin header), then 4 bytes of `n_size = 1` and 4 bytes of nonce.
4. Compute `coinbase_tx_hash = txid(coinbase_tx)` and set `merkle_proof = []` (single-tx block, merkle root equals coinbase txid). Set `parent_block.merkle_root = coinbase_tx_hash`.
5. Set `chain_merkle_proof = []` and `chain_id = 0` (single-entry chain merkle tree).
6. Call `submit_blocks([( doge_hdr, Some(AuxData { coinbase_tx, merkle_proof: [], chain_merkle_proof: [], chain_id: 0, parent_block }) )])`.
7. The contract's `check_aux` computes `scrypt(parent_block_bytes, parent_block_bytes, ...)`, finds it ≤ target, and accepts the block. The fake Dogecoin block is stored as a valid mainchain header. [6](#0-5)

### Citations

**File:** btc-types/src/btc_header.rs (L32-36)
```rust
    #[must_use]
    pub fn block_hash(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        double_sha256(&block_header)
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

**File:** contract/src/dogecoin.rs (L78-154)
```rust
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
