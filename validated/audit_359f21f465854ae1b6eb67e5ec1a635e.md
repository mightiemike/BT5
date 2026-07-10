After thoroughly reviewing the contract source, I can identify a valid analog.

---

### Title
Freed Storage Deposits Permanently Accumulate in Contract With No Withdrawal Mechanism — (`contract/src/lib.rs`)

### Summary

The `submit_blocks()` function collects NEAR token deposits from relayers to cover on-chain storage costs. The `run_mainchain_gc()` function periodically removes old block headers, freeing that storage. In NEAR Protocol, freed storage stake becomes liquid balance in the contract's account. However, no function exists to withdraw this accumulated balance, causing NEAR tokens to be permanently locked in the contract.

### Finding Description

`submit_blocks()` is `#[payable]` and requires a deposit proportional to net storage growth:

```rust
let amount = env::attached_deposit();
let initial_storage = env::storage_usage();
// ...
self.run_mainchain_gc(num_of_headers);
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
require!(amount >= required_deposit, ...);
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id()).transfer(refund).into()
}
``` [1](#0-0) 

GC runs **before** the storage diff is measured, so the deposit required is only for the net positive storage delta. When GC removes more blocks than are added in a batch, `diff_storage_usage` saturates to zero and the full deposit is refunded. But when blocks are added over time and GC removes them in later calls, the NEAR tokens paid for those blocks in earlier calls remain in the contract's balance permanently — freed storage stake becomes liquid contract balance in NEAR Protocol, but no withdrawal path exists.

`run_mainchain_gc()` removes entries from `mainchain_height_to_header`, `mainchain_header_to_height`, and `headers_pool` via `remove_block_header()`:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [2](#0-1) 

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
``` [3](#0-2) 

No function in the contract — not in `lib.rs`, `bitcoin.rs`, `utils.rs`, `dogecoin.rs`, `litecoin.rs`, or `zcash.rs` — allows withdrawing the contract's NEAR balance. The contract struct holds no treasury field and exposes no admin withdrawal method. [4](#0-3) 

### Impact Explanation

Over the operational lifetime of the contract, relayers continuously submit blocks and GC continuously removes old ones (the default `gc_threshold` is 52,704 blocks per year). Each removal cycle frees storage stake that was paid in a prior `submit_blocks()` call. This freed NEAR accumulates in the contract's liquid balance with no mechanism for the protocol, DAO, or any role to recover it. The value lost scales linearly with the number of GC cycles and the storage cost per block header.

### Likelihood Explanation

This is not an attack — it is a structural design gap. Every production deployment running with a finite `gc_threshold` will trigger GC on every `submit_blocks()` call once the chain reaches steady state. The condition is always met in normal operation.

### Recommendation

Add a privileged withdrawal function (e.g., restricted to `Role::DAO`) that transfers the contract's excess liquid NEAR balance to a designated treasury account:

```rust
pub fn withdraw_excess_balance(&mut self, recipient: AccountId, amount: NearToken) {
    // require DAO role
    Promise::new(recipient).transfer(amount)
}
```

Alternatively, track the minimum required storage stake and allow withdrawal of only the surplus above that floor.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100`.
2. Relayer calls `submit_blocks()` with 200 headers, paying the storage deposit for all 200.
3. GC removes 100 old headers within the same call; `diff_storage_usage` reflects only the net +100 headers, so the deposit for the removed 100 headers is retained by the contract.
4. In subsequent calls, as more headers are added and GC removes them, the freed storage deposits accumulate.
5. No call sequence exists that can recover these NEAR tokens from the contract. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L173-197)
```rust
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
```

**File:** contract/src/lib.rs (L377-416)
```rust
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
    }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
