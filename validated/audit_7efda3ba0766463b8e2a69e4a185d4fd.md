### Title
Missing Upper-Bound Validation on `protocol_upgrade_stake_threshold` Permanently Bricks Protocol Upgrade Mechanism — (`core/chain-configs/src/genesis_validate.rs`)

### Summary

`genesis_validate.rs` validates `online_max_threshold` (must be ≤ 1) and `gas_price_adjustment_rate` (must be < 1), but applies **no analogous upper-bound check** to `protocol_upgrade_stake_threshold`. If this `Rational32` field is set to a value greater than 1 (e.g., `[2, 1]`), the epoch-manager's upgrade-voting logic computes a threshold that always exceeds total block-producer stake, making it permanently impossible for any protocol version upgrade to be voted in.

### Finding Description

`validate_genesis` in `genesis_validate.rs` enforces semantic bounds on several `Rational32` fields: [1](#0-0) [2](#0-1) 

However, `protocol_upgrade_stake_threshold` — also a `Rational32` — receives **no upper-bound check** anywhere in `validate_genesis`: [3](#0-2) 

At every epoch boundary, `collect_blocks_info` in the epoch manager reads this field and computes the upgrade threshold: [4](#0-3) 

If `protocol_upgrade_stake_threshold = [2, 1]` (i.e., 200 %), then:

```
threshold = total_block_producer_stake * 2 / 1
          = 2 × total_stake
```

Because `stake ≤ total_stake`, the condition `stake > threshold` is **never true**. The `next_next_epoch_version` always falls back to `protocol_version`, permanently freezing the chain at its current protocol version with no recovery path.

The field flows from `GenesisConfig` into `EpochConfig` without any intermediate guard: [5](#0-4) 

`EpochConfigStore` also loads configs from the file system without validating this field: [6](#0-5) 

### Impact Explanation

A `protocol_upgrade_stake_threshold > 1` permanently prevents any protocol version upgrade from being adopted. Because the upgrade mechanism is the only path to fix epoch-config parameters on a live chain, the chain is irreversibly stuck at its genesis protocol version. All future feature activations, security patches, and resharding events that depend on a protocol version bump become unreachable. This is a **Critical** impact on the protocol upgrade boundary.

### Likelihood Explanation

Low. The field must be misconfigured at genesis or in a custom epoch-config JSON file. Production mainnet and testnet ship with `[4, 5]` (80 %). However, the absence of a validation gate — unlike the explicit guards on `online_max_threshold` and `gas_price_adjustment_rate` — means a misconfigured private chain or a future epoch-config file with a typo (e.g., `[5, 4]` = 125 %) silently bricks upgrades with no error at startup.

### Recommendation

Add an upper-bound check in `validate_processed_records` (or a dedicated config-level check) analogous to the existing `online_max_threshold` guard:

```rust
if self.genesis_config.protocol_upgrade_stake_threshold > Rational32::from_integer(1) {
    let error_message = format!(
        "protocol_upgrade_stake_threshold must be <= 1, but current value is {}",
        self.genesis_config.protocol_upgrade_stake_threshold
    );
    self.validation_errors.push_genesis_semantics_error(error_message)
}
```

Apply the same check when loading `EpochConfig` from the file system in `EpochConfigStore::load_epoch_config_from_file_system` and `load_default_epoch_configs`.

### Proof of Concept

1. Set genesis `protocol_upgrade_stake_threshold` to `[2, 1]` (serialised as `"protocol_upgrade_stake_threshold": [2, 1]`).
2. `Genesis::new` calls `validate_genesis`; no error is raised because no upper-bound check exists for this field.
3. The chain starts normally.
4. At every epoch boundary, `collect_blocks_info` computes `threshold = 2 × total_stake`.
5. The maximum possible `stake` for any version is `total_stake`; `total_stake > 2 × total_stake` is always false.
6. `next_next_epoch_version` is always set to the current `protocol_version`.
7. No protocol upgrade can ever be adopted; the chain is permanently frozen at its genesis protocol version. [7](#0-6) [8](#0-7)

### Citations

**File:** core/chain-configs/src/genesis_validate.rs (L85-193)
```rust
    pub fn validate_processed_records(&mut self) {
        let validators = self
            .genesis_config
            .validators
            .clone()
            .into_iter()
            .map(|account_info| {
                if !is_valid_staking_key(&account_info.public_key) {
                    let error_message = format!("validator staking key is not valid");
                    self.validation_errors.push_genesis_semantics_error(error_message);
                }
                (account_info.account_id, account_info.amount)
            })
            .collect::<HashMap<_, _>>();

        if validators.len() != self.genesis_config.validators.len() {
            let error_message = format!(
                "Duplicate account in validators. The number of account_ids: {} does not match the number of validators: {}.",
                self.account_ids.len(),
                validators.len()
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if validators.is_empty() {
            let error_message = format!("No validators in genesis");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if self.total_supply != self.genesis_config.total_supply {
            let error_message = format!(
                "wrong total supply. account.locked() + account.amount() = {} is not equal to the total supply = {} specified in genesis config.",
                self.total_supply, self.genesis_config.total_supply
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if validators != self.staked_accounts {
            let error_message = format!("Validator accounts do not match staked accounts.");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        for account_id in &self.access_key_account_ids {
            if !self.account_ids.contains(account_id) {
                let error_message = format!("access key account {} does not exist", account_id);
                self.validation_errors.push_genesis_semantics_error(error_message)
            }
        }

        for account_id in &self.contract_account_ids {
            if !self.account_ids.contains(account_id) {
                let error_message = format!("contract account {} does not exist,", account_id);
                self.validation_errors.push_genesis_semantics_error(error_message)
            }
        }

        if self.genesis_config.online_max_threshold <= self.genesis_config.online_min_threshold {
            let error_message = format!(
                "Online max threshold {} smaller than min threshold {}",
                self.genesis_config.online_max_threshold, self.genesis_config.online_min_threshold
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if self.genesis_config.online_max_threshold > Rational32::from_integer(1) {
            let error_message = format!(
                "Online max threshold must be less or equal than 1, but current value is {}",
                self.genesis_config.online_max_threshold
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if *self.genesis_config.online_max_threshold.numer() >= 10_000_000 {
            let error_message =
                format!("online_max_threshold's numerator is too large, may lead to overflow.");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if *self.genesis_config.online_min_threshold.numer() >= 10_000_000 {
            let error_message =
                format!("online_min_threshold's numerator is too large, may lead to overflow.");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if *self.genesis_config.online_max_threshold.denom() >= 10_000_000 {
            let error_message =
                format!("online_max_threshold's denominator is too large, may lead to overflow.");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if *self.genesis_config.online_min_threshold.denom() >= 10_000_000 {
            let error_message =
                format!("online_min_threshold's denominator is too large, may lead to overflow.");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if self.genesis_config.gas_price_adjustment_rate >= Rational32::from_integer(1) {
            let error_message = format!(
                "Gas price adjustment rate must be less than 1, value in config is {}",
                self.genesis_config.gas_price_adjustment_rate
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }

        if self.genesis_config.epoch_length == 0 {
            let error_message = format!("Epoch Length must be greater than 0");
            self.validation_errors.push_genesis_semantics_error(error_message)
        }
    }
```

**File:** chain/epoch-manager/src/lib.rs (L623-636)
```rust
        let next_next_epoch_version = if let Some((version, stake)) =
            versions.into_iter().max_by_key(|&(_version, stake)| stake)
        {
            let numer = *config.protocol_upgrade_stake_threshold.numer() as u128;
            let denom = *config.protocol_upgrade_stake_threshold.denom() as u128;
            let threshold = Balance::from_yoctonear(
                (U256::from(total_block_producer_stake.as_yoctonear()) * U256::from(numer)
                    / U256::from(denom))
                .as_u128(),
            );
            if stake > threshold { version } else { protocol_version }
        } else {
            protocol_version
        };
```

**File:** core/chain-configs/src/genesis_config.rs (L243-270)
```rust
impl From<&GenesisConfig> for EpochConfig {
    fn from(config: &GenesisConfig) -> Self {
        EpochConfigBuilder::default()
            .epoch_length(config.epoch_length)
            .shard_layout(config.shard_layout.clone())
            .num_block_producer_seats(config.num_block_producer_seats)
            .num_chunk_producer_seats(config.num_chunk_producer_seats)
            .num_chunk_validator_seats(config.num_chunk_validator_seats)
            .target_validator_mandates_per_shard(config.target_validator_mandates_per_shard)
            .minimum_validators_per_shard(config.minimum_validators_per_shard)
            .block_producer_kickout_threshold(config.block_producer_kickout_threshold)
            .chunk_producer_kickout_threshold(config.chunk_producer_kickout_threshold)
            .chunk_validator_only_kickout_threshold(config.chunk_validator_only_kickout_threshold)
            .validator_max_kickout_stake_perc(config.max_kickout_stake_perc)
            .online_min_threshold(config.online_min_threshold)
            .online_max_threshold(config.online_max_threshold)
            .fishermen_threshold(config.fishermen_threshold)
            .protocol_upgrade_stake_threshold(config.protocol_upgrade_stake_threshold)
            .minimum_stake_divisor(config.minimum_stake_divisor)
            .minimum_stake_ratio(config.minimum_stake_ratio)
            .chunk_producer_assignment_changes_limit(config.chunk_producer_assignment_changes_limit)
            .shuffle_shard_assignment_for_chunk_producers(
                config.shuffle_shard_assignment_for_chunk_producers,
            )
            .max_inflation_rate(config.max_inflation_rate)
            .build()
            .expect("field init missing")
    }
```

**File:** core/primitives/src/epoch_manager.rs (L483-510)
```rust
    /// Reads the json files from the epoch config directory.
    fn load_epoch_config_from_file_system(
        directory: &str,
    ) -> BTreeMap<ProtocolVersion, Arc<EpochConfig>> {
        fn get_epoch_config(
            dir_entry: fs::DirEntry,
        ) -> Option<(ProtocolVersion, Arc<EpochConfig>)> {
            let path = dir_entry.path();
            if !(path.extension()? == "json") {
                return None;
            }
            let file_name = path.file_stem()?.to_str()?.to_string();
            let protocol_version = file_name.parse().expect("Invalid protocol version");
            if protocol_version > PROTOCOL_VERSION {
                return None;
            }
            let contents = fs::read_to_string(&path).ok()?;
            let epoch_config = serde_json::from_str(&contents).unwrap_or_else(|_| {
                panic!("Failed to parse epoch config for version {}", protocol_version)
            });
            Some((protocol_version, epoch_config))
        }

        fs::read_dir(directory)
            .expect("Failed opening epoch config directory")
            .filter_map(Result::ok)
            .filter_map(get_epoch_config)
            .collect()
```
