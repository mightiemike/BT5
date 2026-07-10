### Title
`get_header_by_height` reads height-only `mainchain_height_to_header` during fork difficulty validation, silently substituting the mainchain ancestor for the fork's true ancestor — (`contract/src/lib.rs`, `contract/src/dogecoin.rs`)

---

### Summary

The difficulty adjustment algorithms for all supported chains call `get_header_by_height(height_first)` to retrieve the block at the start of the difficulty period. This function reads from `mainchain_height_to_header`, which is keyed only by block height and always returns the current **mainchain** block at that height. When validating a fork block whose chain diverged before `height_first`, the function silently returns the mainchain's block instead of the fork's true ancestor, producing an incorrect difficulty target. An unprivileged relayer can exploit this to submit fork blocks with a `bits` value that satisfies the wrong (easier) difficulty, bypassing the protocol's PoW security invariant.

---

### Finding Description

`mainchain_height_to_header: LookupMap<u64, H256>` is keyed only by block height, with no chain-identity discriminator. [1](#0-0) 

`get_header_by_height` reads exclusively from this map: [2](#0-1) 

This function is the sole mechanism used by every chain's difficulty adjustment to look up the block at the start of the difficulty period:

- **Bitcoin** (`bitcoin.rs:81`): `blocks_getter.get_header_by_height(first_block_height)` — called at every 2016-block boundary. [3](#0-2) 

- **Dogecoin** (`dogecoin.rs:292–295`): `blocks_getter.get_header_by_height(height_first)` — called for **every block** (per-block Digishield). The developers themselves flagged this with an explicit TODO:

<cite repo="Thankgodd

### Citations

**File:** contract/src/lib.rs (L98-98)
```rust
    mainchain_height_to_header: LookupMap<u64, H256>,
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
