### Title
Non-Injective Zcash Header Decoding Silently Ignores Compact-Size Prefix Bytes, Causing Incorrect Block Hash Storage - (File: `btc-types/src/zcash_header.rs`)

---

### Summary

`Header::from_block_header_vec` in `zcash_header.rs` silently skips the 3-byte compact-size prefix (`[0xfd, 0x40, 0x05]`) at byte positions 140–142 without reading or validating them. Meanwhile, `get_block_header_vec` (used by `block_hash()` and `block_hash_pow()`) always writes the canonical `[0xfd, 0x40, 0x05]` bytes at those positions. This creates a non-injective decode: 2²⁴ distinct byte sequences at positions 140–142 all decode to the same `Header` struct, but re-encode to a single canonical byte sequence. The block hash computed and stored on-chain therefore does not correspond to the actual Zcash block hash derived from the submitted bytes.

---

### Finding Description

The Zcash header layout is:

| Field | Bytes | Range |
|---|---|---|
| version | 4 | 0..4 |
| prev_block_hash | 32 | 4..36 |
| merkle_root | 32 | 36..68 |
| block_commitments | 32 | 68..100 |
| time | 4 | 100..104 |
| bits | 4 | 104..108 |
| nonce | 32 | 108..140 |
| compact-size prefix | 3 | 140..143 |
| solution | 1344 | 143..1487 |

In `from_block_header_vec`, after reading `nonce` from `[108..140]`, the function jumps directly to `block_header[143..]` for the solution:

```rust
let nonce = H256::try_from(&block_header[108..140]).map_err(|_| Error::InvalidLength)?;
let solution = block_header[143..].to_vec();
```

Bytes 140–142 are never read or validated. [1](#0-0) 

In contrast, `get_block_header_vec` always writes the canonical prefix:

```rust
block_header.extend_from_slice(&[0xfd, 0x40, 0x05]);
block_header.extend_from_slice(&self.solution);
``` [2](#0-1) 

`block_hash()` and `block_hash_pow()` both call `get_block_header_vec()`, so the hash is always computed over the canonical prefix, regardless of what bytes were submitted at positions 140–142. [3](#0-2) 

---

### Impact Explanation

When a relayer submits a Zcash header via `submit_blocks`, the contract calls `Header::from_block_header_vec` to parse it, then calls `header.block_hash()` to derive the canonical identifier stored in `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`. [4](#0-3) 

Because `block_hash()` always uses the canonical `[0xfd, 0x40, 0x05]` bytes, a header submitted with any other 3 bytes at positions 140–142 will be stored under a hash that does not match the actual Zcash network block hash (which is computed over the submitted bytes). The contract's canonical chain therefore references phantom block hashes that do not exist in the Zcash blockchain. Any downstream SPV proof verified against `header.block_header.merkle_root` via `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` would be anchored to a block hash that is not verifiable on the Zcash network. [5](#0-4) 

Additionally, the PoW check in `submit_block_header` computes `header.block_hash_pow()` using the canonical bytes, not the submitted bytes, so the hash-based PoW check is performed against a value that does not correspond to the submitted header. [6](#0-5) 

---

### Likelihood Explanation

The entry point is `submit_blocks`, which is gated by `#[trusted_relayer]`. Any staked relayer participant can reach this code path — this is the primary production submission path for the Zcash build. A malicious or buggy relayer submitting headers with non-canonical compact-size bytes (e.g., `[0x00, 0x00, 0x00]` instead of `[0xfd, 0x40, 0x05]`) would silently corrupt the stored canonical chain without triggering any error. [7](#0-6) 

---

### Recommendation

In `from_block_header_vec`, explicitly read and validate the 3-byte compact-size prefix before extracting the solution:

```rust
let compact_size = &block_header[140..143];
if compact_size != [0xfd, 0x40, 0x05] {
    return Err(Error::InvalidLength);
}
let solution = block_header[143..].to_vec();
``` [8](#0-7) 

This ensures the decode is injective: only the canonical byte sequence is accepted, and the hash computed on-chain matches the actual Zcash block hash.

---

### Proof of Concept

The following demonstrates the non-injective property:

```rust
// Canonical header bytes (positions 140-142 = [0xfd, 0x40, 0x05])
let canonical_bytes: Vec<u8> = /* valid 1487-byte Zcash header */ ...;

// Mutated header bytes (positions 140-142 = [0x00, 0x00, 0x00])
let mut mutated_bytes = canonical_bytes.clone();
mutated_bytes[140] = 0x00;
mutated_bytes[141] = 0x00;
mutated_bytes[142] = 0x00;

let canonical_header = Header::from_block_header_vec(&canonical_bytes).unwrap();
let mutated_header  = Header::from_block_header_vec(&mutated_bytes).unwrap();

// Both decode to identical Header structs
assert_eq!(canonical_header, mutated_header);

// Both produce the same block_hash (canonical bytes used internally)
assert_eq!(canonical_header.block_hash(), mutated_header.block_hash());

// But the actual Zcash network hash of mutated_bytes differs from block_hash()
// because the network hashes the submitted bytes, not the canonical ones.
```

The contract would store `mutated_header.block_hash()` — a hash computed over `[0xfd, 0x40, 0x05]` — as the canonical chain entry for a header that was submitted with `[0x00, 0x00, 0x00]`, producing a stored chain state that does not correspond to any real Zcash block. [8](#0-7)

### Citations

**File:** btc-types/src/zcash_header.rs (L38-46)
```rust
    pub fn block_hash(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        double_sha256(&block_header)
    }

    pub fn block_hash_pow(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        double_sha256(&block_header)
    }
```

**File:** btc-types/src/zcash_header.rs (L57-58)
```rust
        block_header.extend_from_slice(&[0xfd, 0x40, 0x05]); // The compact size of an Equihash solution in bytes (always 1344).
        block_header.extend_from_slice(&self.solution);
```

**File:** btc-types/src/zcash_header.rs (L76-116)
```rust
    pub fn from_block_header_vec(block_header: &[u8]) -> Result<Self, Error> {
        if block_header.len() != Self::SIZE {
            return Err(Error::InvalidLength);
        }

        let version = i32::from_le_bytes(
            block_header[0..4]
                .try_into()
                .map_err(|_| Error::IntParseError)?,
        );
        let prev_block_hash =
            H256::try_from(&block_header[4..36]).map_err(|_| Error::InvalidLength)?;
        let merkle_root =
            H256::try_from(&block_header[36..68]).map_err(|_| Error::InvalidLength)?;

        let block_commitments =
            H256::try_from(&block_header[68..100]).map_err(|_| Error::InvalidLength)?;
        let time = u32::from_le_bytes(
            block_header[100..104]
                .try_into()
                .map_err(|_| Error::IntParseError)?,
        );
        let bits = u32::from_le_bytes(
            block_header[104..108]
                .try_into()
                .map_err(|_| Error::IntParseError)?,
        );
        let nonce = H256::try_from(&block_header[108..140]).map_err(|_| Error::InvalidLength)?;
        let solution = block_header[143..].to_vec();

        Ok(Self {
            version,
            prev_block_hash,
            merkle_root,
            block_commitments,
            time,
            bits,
            nonce,
            solution,
        })
    }
```

**File:** contract/src/lib.rs (L167-172)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L318-323)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L503-515)
```rust
        let current_block_hash = header.block_hash();

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };
```

**File:** contract/src/lib.rs (L520-525)
```rust
            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
```
