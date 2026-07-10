### Title
Wrong Hash Function Used for AuxPoW Parent Block PoW Verification - (`File: contract/src/dogecoin.rs`)

### Summary
In the Dogecoin AuxPoW verification path, `check_aux()` computes the parent block's PoW hash using `block_hash_pow()`, which resolves to **Scrypt** when the `scrypt_hash` feature is compiled in (as it is for Dogecoin builds). The parent block in AuxPoW is a Bitcoin block whose PoW must be verified with **SHA-256d**, not Scrypt. This is a direct analog to the "wrong asset" class: the wrong cryptographic primitive is applied to a critical security check, allowing an attacker to forge a passing PoW proof with a parent block that has no valid SHA-256d work.

---

### Finding Description

In `check_aux()`, the parent block's PoW hash is obtained via:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    format!("block should have correct pow")
);
``` [1](#0-0) 

`block_hash_pow()` is defined in `btc-types/src/btc_header.rs` with a compile-time branch:

```rust
pub fn block_hash_pow(&self) -> H256 {
    let block_header = self.get_block_header_vec();
    #[cfg(feature = "scrypt_hash")]
    {
        // Scrypt path — used for Dogecoin builds
        scrypt::scrypt(&block_header, &block_header, &params, &mut output).unwrap();
        H256::from(output)
    }
    #[cfg(not(feature = "scrypt_hash"))]
    {
        double_sha256(&block_header)
    }
}
``` [2](#0-1) 

When the contract is compiled for Dogecoin (with `scrypt_hash` enabled), every call to `block_hash_pow()` — including the one on `aux_data.parent_block` — uses Scrypt. But `aux_data.parent_block` is a Bitcoin block:

```rust
pub struct AuxData {
    pub coinbase_tx: Vec<u8>,
    pub merkle_proof: Vec<H256>,
    pub chain_merkle_proof: Vec<H256>,
    pub chain_id: usize,
    pub parent_block: Header,   // Bitcoin block — SHA-256d, not Scrypt
}
``` [3](#0-2) 

Bitcoin's PoW is SHA-256d. Applying Scrypt to a Bitcoin block header produces an unrelated 32-byte value. The PoW check therefore does not verify that the parent block has valid Bitcoin work; it verifies a meaningless Scrypt digest instead.

---

### Impact Explanation

An attacker can submit a Dogecoin AuxPoW block with a crafted parent block whose **Scrypt hash** is below the Dogecoin target, while its **SHA-256d hash** carries no real Bitcoin work. Because the contract checks the Scrypt digest, the forged parent block passes the PoW gate. All other AuxPoW structural checks (coinbase merkle proof, chain merkle proof, chain-ID index) are independent of the hash function and can be satisfied legitimately. The result is that the contract accepts a Dogecoin block backed by zero real Bitcoin hashrate, corrupting the canonical chain state stored in `mainchain_tip_blockhash` and the `mainchain_height_to_header` / `mainchain_header_to_height` maps. Downstream consumers calling `verify_transaction_inclusion` against this fraudulent tip will receive incorrect inclusion proofs.

**Impact: High** — canonical chain state is permanently corrupted; transaction inclusion proofs become forgeable.

---

### Likelihood Explanation

The Dogecoin network target is orders of magnitude easier than Bitcoin's. An attacker needs only to find a parent block header whose Scrypt hash falls below the current Dogecoin target. Because Scrypt with N=1024 (the parameters in `block_hash_pow`) is far cheaper than Bitcoin SHA-256d mining, and the required threshold is the Dogecoin difficulty (not Bitcoin's), this is computationally feasible for any well-resourced attacker. No privileged role, leaked key, or social engineering is required — only a call to the public `submit_blocks()` entry point.

**Likelihood: High**

---

### Recommendation

Replace the call to `block_hash_pow()` on the parent block with an explicit `double_sha256` call, since the parent block is always a Bitcoin (SHA-256d) block regardless of which chain the contract is compiled for:

```rust
// Before (wrong — uses Scrypt when scrypt_hash feature is enabled):
let pow_hash = aux_data.parent_block.block_hash_pow();

// After (correct — always SHA-256d for the Bitcoin parent block):
let pow_hash = btc_types::hash::double_sha256(
    &aux_data.parent_block.get_block_header_vec()
);
```

Alternatively, expose a dedicated `block_hash_sha256d()` method on `Header` that is unconditionally SHA-256d, and use it exclusively for AuxPoW parent block verification.

---

### Proof of Concept

1. Attacker constructs a Dogecoin block header with the AuxPoW flag set (`version & 0x100 != 0`) and a valid chain ID.
2. Attacker brute-forces a parent block header (80 bytes) until its **Scrypt** hash (N=1024, r=1, p=1) is below `target_from_bits(dogecoin_block.bits)`. This requires work proportional to Dogecoin difficulty, not Bitcoin difficulty.
3. Attacker builds a coinbase transaction embedding the Dogecoin block hash as the chain merkle root (single-element chain merkle tree, `chain_merkle_proof = []`, `chain_id = 0`).
4. Attacker calls `submit_blocks()` with the crafted `(Header, Some(AuxData))` pair.
5. `check_aux()` computes `Scrypt(parent_block)` — which the attacker has arranged to be below the Dogecoin target — and the `require!` passes.
6. The block is stored as the new `mainchain_tip_blockhash` with no real Bitcoin hashrate behind it. [1](#0-0) [2](#0-1)

### Citations

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
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
