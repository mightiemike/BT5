### Title
`update_dynamic_config` Applies `ContextDynamicConfig` Without Validating Sort-Order Invariant Required by `get_min_gas_price_for_height` - (`File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs`)

### Summary
`get_min_gas_price_for_height` relies on `min_l2_gas_price_per_height` being sorted in strictly ascending order by height. The validation function `validate_dynamic_config` enforces this invariant, but `update_dynamic_config` — the live hot-reload path — assigns a freshly fetched `ContextDynamicConfig` directly to `self.config.dynamic_config` without ever calling `.validate()`. If the config manager returns an unsorted list (misconfiguration, file edit, or bug), the reverse-scan lookup silently returns the wrong minimum gas price, and every subsequent block is produced with an incorrect `next_l2_gas_price`.

### Finding Description
`get_min_gas_price_for_height` iterates the slice in reverse and returns the price of the first entry whose `height <= query_height`:

```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs
pub fn get_min_gas_price_for_height(
    height: BlockNumber,
    min_l2_gas_price_per_height: &[PricePerHeight],
) -> GasPrice {
    let fallback_min_gas_price = VersionedConstants::latest_constants().min_gas_price;
    min_l2_gas_price_per_height
        .iter()
        .rev()
        .find(|e| e.height <= height.0)
        .map(|e| GasPrice(e.price))
        .unwrap_or(fallback_min_gas_price)
}
```

The docstring explicitly states the precondition: *"assumed to be sorted by height in ascending order."* [1](#0-0) 

The validation that enforces this invariant lives in `validate_dynamic_config`:

```rust
fn validate_dynamic_config(config: &ContextDynamicConfig) -> Result<(), ...> {
    if !config.min_l2_gas_price_per_height.windows(2).all(|w| w[0].height < w[1].height) {
        return Err(...);
    }
    ...
}
``` [2](#0-1) 

However, the live hot-reload path `update_dynamic_config` assigns the fetched config without calling `.validate()`:

```rust
async fn update_dynamic_config(&mut self) {
    if let Some(config_manager_client) = self.deps.config_manager_client.clone() {
        let config_result = config_manager_client.get_context_dynamic_config().await;
        match config_result {
            Ok(config) => {
                self.config.dynamic_config = config;  // ← no .validate() call
            }
            ...
        }
    }
}
``` [3](#0-2) 

The deserialization path (`deserialize_price_per_height_from_string` → `parse_price_per_height`) also does not validate sort order — it only parses the `height:price` pairs: [4](#0-3) 

**Concrete wrong-value scenario:**

Config file (or config manager response) contains:
```
min_l2_gas_price_per_height = "500:20000000000,100:10000000000"
```
(entries in descending order — a plausible operator mistake)

For `height = 600`:
- Reverse scan hits `(100, 10G)` first; `100 <= 600` → returns `GasPrice(10G)`.
- Correct answer: `(500, 20G)` applies → should return `GasPrice(20G)`.

The wrong minimum `10G` is then used as `effective_min` in `calculate_next_base_gas_price`, and the resulting `next_l2_gas_price` is committed into every subsequent block header. [5](#0-4) 

### Impact Explanation
The wrong `next_l2_gas_price` is embedded in the block's `FeeMarketInfo` and propagated to all validators and RPC clients. Fee estimation, simulation, and pending-block views all read this value and return authoritative-looking wrong gas prices. If the wrong minimum is lower than the intended minimum, the sequencer accepts and prices transactions below the protocol floor, creating an economic impact across all blocks produced until the config is corrected.

### Likelihood Explanation
The trigger is a config file or config manager response with an unsorted `min_l2_gas_price_per_height`. This can occur through an operator typo, a config management pipeline bug, or a compromised config manager. The `ConfigManagerRunner` watches for file changes and applies them automatically on every periodic tick, so the window for the wrong value to take effect is small. The absence of `.validate()` in `update_dynamic_config` means there is no runtime guard.

### Recommendation
Call `.validate()` on the fetched config inside `update_dynamic_config` before assigning it, and reject (log + skip) configs that fail validation:

```rust
async fn update_dynamic_config(&mut self) {
    if let Some(config_manager_client) = self.deps.config_manager_client.clone() {
        let config_result = config_manager_client.get_context_dynamic_config().await;
        match config_result {
            Ok(config) => {
                if let Err(e) = config.validate() {
                    error!("Fetched dynamic config is invalid, not applying: {e:?}");
                    return;
                }
                self.config.dynamic_config = config;
            }
            Err(e) => { error!(...); }
        }
    }
}
```

Additionally, `parse_price_per_height` should validate sort order at parse time so that the invariant is enforced at every entry point.

### Proof of Concept
1. Write a config file with `min_l2_gas_price_per_height = "500:20000000000,100:10000000000"` (descending order).
2. Start the node; `ConfigManagerRunner` reads and deserializes the file — `parse_price_per_height` succeeds without error.
3. `update_dynamic_config` is called on the next height transition; the unsorted config is assigned without validation.
4. At block height 600, `get_min_gas_price_for_height(600, &[(500,20G),(100,10G)])` iterates in reverse, finds `(100,10G)` first (`100 <= 600`), and returns `GasPrice(10G)` instead of `GasPrice(20G)`.
5. `calculate_next_base_gas_price` uses `10G` as `min_gas_price`; the resulting `next_l2_gas_price` in the block header is `10G` instead of the intended `20G`.
6. All RPC fee-estimation calls for subsequent blocks return prices anchored to the wrong minimum.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L33-52)
```rust
/// Get the minimum gas price for a given block height from the min_l2_gas_price_per_height
/// configuration. If not exist for the given height, use versioned constants min_gas_price as
/// fallback.
///
/// # Parameters
/// - `height`: The block height to look up.
/// - `min_l2_gas_price_per_height`: List of height-price pairs from configuration, assumed to be
///   sorted by height in ascending order.
pub fn get_min_gas_price_for_height(
    height: BlockNumber,
    min_l2_gas_price_per_height: &[PricePerHeight],
) -> GasPrice {
    let fallback_min_gas_price = VersionedConstants::latest_constants().min_gas_price;
    min_l2_gas_price_per_height
        .iter()
        .rev()
        .find(|e| e.height <= height.0)
        .map(|e| GasPrice(e.price))
        .unwrap_or(fallback_min_gas_price)
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L54-77)
```rust
/// Compute the next L2 gas price (for the fin or for updating state). Respects override when set.
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L106-133)
```rust
pub fn parse_price_per_height(s: &str) -> Result<Vec<PricePerHeight>, String> {
    let trimmed = s.trim();

    if trimmed.is_empty() {
        return Ok(Vec::new());
    }

    trimmed
        .split(',')
        .map(|entry| {
            let entry = entry.trim();
            let parts: Vec<&str> = entry.split(':').map(|p| p.trim()).collect();
            if parts.len() != 2 {
                return Err(format!(
                    "Invalid price_per_height entry format: '{}'. Expected 'height:price'",
                    entry
                ));
            }
            let height = parts[0]
                .parse::<u64>()
                .map_err(|e| format!("Invalid height '{}': {}", parts[0], e))?;
            let price = parts[1]
                .parse::<u128>()
                .map_err(|e| format!("Invalid price '{}': {}", parts[1], e))?;
            Ok(PricePerHeight { height, price })
        })
        .collect()
}
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L463-484)
```rust
fn validate_dynamic_config(
    config: &ContextDynamicConfig,
) -> Result<(), validator::ValidationError> {
    // Check that heights are in strictly ascending order using windows
    if !config.min_l2_gas_price_per_height.windows(2).all(|w| w[0].height < w[1].height) {
        return Err(validator::ValidationError::new(
            "min_l2_gas_price_per_height heights must be in strictly ascending order",
        ));
    }

    // Check that all prices are above the minimum
    for entry in &config.min_l2_gas_price_per_height {
        if entry.price < MIN_ALLOWED_GAS_PRICE {
            return Err(validator::ValidationError::new(
                "all prices in min_l2_gas_price_per_height must be at least 8 gwei (8000000000 \
                 fri)",
            ));
        }
    }

    Ok(())
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1274-1289)
```rust
    async fn update_dynamic_config(&mut self) {
        if let Some(config_manager_client) = self.deps.config_manager_client.clone() {
            let config_result = config_manager_client.get_context_dynamic_config().await;
            match config_result {
                Ok(config) => {
                    self.config.dynamic_config = config;
                }
                Err(e) => {
                    error!(
                        "Failed to get dynamic config for consensus context. Config not updated. \
                         Error: {e:?}"
                    );
                }
            }
        }
    }
```
