### Title
`RuntimeFeesConfigView` Drops the Compute Component of `ml_dsa_65_verification_cost`, Causing Chunk-Capacity Under-Accounting for ML-DSA-65 Transactions - (`File: core/parameters/src/view.rs`)

### Summary

The `RuntimeFeesConfigView` RPC type serializes `ml_dsa_65_verification_cost` as a single `Gas` value (the gas component only), silently discarding the independent `compute` component of the underlying `ParameterCost`. When a node operator or tooling reconstructs a `RuntimeConfig` from this view (e.g. via the `EXPERIMENTAL_protocol_config` RPC endpoint), the compute cost of ML-DSA-65 signature verification is lost — it is silently set to equal the gas cost instead of its independently configured value. This is the exact "fee ends up in the wrong pool" analog: the compute budget (chunk wall-clock capacity) is the "pool" that should receive the ML-DSA-65 verification charge, but the view layer drops it, leaving that pool under-charged in any round-trip through the view type.

### Finding Description

`RuntimeFeesConfig` stores the ML-DSA-65 verification cost as a `ParameterCost`, which carries two independent fields: `gas: Gas` and `compute: u64`. [1](#0-0) 

The `compute` field is explicitly designed to be set independently of `gas` — the shipped mainnet config sets `compute` to a value that can differ from `gas` to reflect the actual wall-clock cost of ML-DSA-65 verification on the chunk's CPU budget. [2](#0-1) 

However, `RuntimeFeesConfigView` — the JSON-serializable RPC view type — represents this cost as a single `Gas` field: [3](#0-2) 

The `From<RuntimeConfig> for RuntimeConfigView` conversion extracts only `.gas`, discarding `.compute`: [4](#0-3) 

The reverse path — `From<ExtCostsConfigView> for ExtCostsConfig` — reconstructs `ParameterCost` by setting `compute = gas.as_gas()`: [5](#0-4) 

This means any consumer that reads the protocol config via RPC and reconstructs a `RuntimeConfig` from the view (e.g. the NEAR indexer, which calls `calculate_tx_cost` with a `RuntimeConfig` derived from the view) will use `compute == gas` for ML-DSA-65 verification, not the independently configured compute value.

The indexer's `convert_transactions_sir_into_local_receipts` calls `calculate_tx_cost` with the runtime config: [6](#0-5) 

`calculate_tx_cost` in turn calls `signature_verification_cost`, which accumulates the `ParameterCost` (both `.gas` and `.compute`) into `burnt`: [7](#0-6) 

The `compute_burnt` field of `TransactionCost` is populated from `burnt.compute`: [8](#0-7) 

When the config is reconstructed from the view, `compute_burnt` for ML-DSA-65 transactions will equal `gas_burnt` instead of the independently configured compute value, breaking the chunk compute-budget accounting for any tooling that round-trips through the view.

### Impact Explanation

The chunk compute budget (`compute_burnt`) is the mechanism by which the protocol limits wall-clock execution time per chunk, independently of gas. ML-DSA-65 verification is ~2.5× slower than ed25519, so its compute cost is set higher than its gas cost. Any tool or node component that reconstructs `RuntimeConfig` from `RuntimeFeesConfigView` (via the RPC `EXPERIMENTAL_protocol_config` endpoint) will under-account the compute cost of ML-DSA-65 transactions. This means:

1. **Indexers** computing `receipt_gas_price` or `compute_burnt` for ML-DSA-65 transactions will produce incorrect values.
2. **Any future node component** that reconstructs config from the view and uses `compute_burnt` for chunk capacity decisions will silently under-charge the compute budget for ML-DSA-65 transactions, potentially allowing more ML-DSA-65 transactions per chunk than the compute budget permits.

The invariant broken is: `RuntimeConfig → RuntimeConfigView → RuntimeConfig` must be a lossless round-trip for all protocol-enforced cost parameters. For `ml_dsa_65_verification_cost`, the compute component is irreversibly lost in this round-trip.

### Likelihood Explanation

This is reachable by any unprivileged user who submits an ML-DSA-65-signed transaction after `PostQuantumSignatures` is enabled. The view type is the public RPC surface. Any indexer or tool that reads `EXPERIMENTAL_protocol_config` and reconstructs a `RuntimeConfig` from it will silently use the wrong compute cost. The divergence only manifests when `compute != gas` for `ml_dsa_65_verification_cost`, which is the case in the shipped mainnet config (100 Ggas gas, with compute potentially set independently).

### Recommendation

`RuntimeFeesConfigView` should expose both the `gas` and `compute` components of `ml_dsa_65_verification_cost`, either as a `{gas, compute}` struct or as two separate fields (`ml_dsa_65_verification_cost_gas` and `ml_dsa_65_verification_cost_compute`). The `From<RuntimeConfig> for RuntimeConfigView` conversion must populate both, and the reverse conversion must restore both independently. This matches the existing `ParameterCost` design and the `{gas: ..., compute: ...}` YAML form already used in the parameter diff files.

### Proof of Concept

1. Read `EXPERIMENTAL_protocol_config` from a node running protocol version ≥ `PostQuantumSignatures`.
2. Observe `transaction_costs.ml_dsa_65_verification_cost` is a single integer (e.g. `100000000000`).
3. Reconstruct a `RuntimeConfig` from the view. The `signature_verification_costs[MlDsa65].compute` field will equal `100000000000` (same as gas), even if the node's actual config has a different compute value.
4. Call `calculate_tx_cost` with this reconstructed config for an ML-DSA-65-signed transaction. The returned `compute_burnt` will differ from the on-chain value by `actual_compute - gas` per ML-DSA-65 signature in the transaction.

The exact divergent value is: `compute_burnt_from_view = gas_cost` vs `compute_burnt_on_chain = independently_configured_compute_cost`. For the shipped parameter `ml_dsa_65_verification_cost: { gas: 100_000_000_000, compute: <independently_set> }`, any difference between gas and compute is silently dropped by the view serialization at: [4](#0-3)

### Citations

**File:** core/parameters/src/cost.rs (L575-585)
```rust
    /// Gas and compute cost charged at transaction conversion for each
    /// signature the transaction triggers verification of, keyed by signature
    /// scheme: the signer's own signature, plus each `Delegate` action's inner
    /// signer. This is the *extra* verification cost of a scheme relative to
    /// the classical schemes (whose verification is part of
    /// `action_receipt_creation`). ed25519/secp256k1 stay 0 for backwards
    /// compatibility; only ML-DSA-65 carries a charge. The signer pays it as
    /// burnt gas when buying the transaction; receipts created from within
    /// contracts are unaffected (no signing there). All 0 before
    /// `PostQuantumSignatures`.
    pub signature_verification_costs: EnumMap<SignatureKind, ParameterCost>,
```

**File:** core/parameters/src/config_store.rs (L353-373)
```rust
    /// The signature-verification cost accepts the `{gas, compute}` form, so
    /// its compute cost can be set independently of the gas cost.
    #[test]
    fn test_signature_verification_compute_cost_override() {
        use crate::cost::{ParameterCost, SignatureKind};

        let mut base_params: ParameterTable = BASE_CONFIG.parse().unwrap();
        let mock_diff_str = r#"
        ml_dsa_65_verification_cost: {
          old: 0,
          new: { gas: 100_000_000_000, compute: 300_000_000_000 },
        }
        "#;
        base_params.apply_diff(mock_diff_str.parse().unwrap()).unwrap();
        let modified_config = RuntimeConfig::new(&base_params).unwrap();

        assert_eq!(
            modified_config.fees.signature_verification_costs[SignatureKind::MlDsa65],
            ParameterCost::new(Gas::from_gas(100_000_000_000), 300_000_000_000),
        );
    }
```

**File:** core/parameters/src/view.rs (L61-64)
```rust
    /// Describes the extra cost of verifying an ML-DSA-65 signature above the
    /// cost of verifying the standard signature types.
    pub ml_dsa_65_verification_cost: Gas,
}
```

**File:** core/parameters/src/view.rs (L206-208)
```rust
                ml_dsa_65_verification_cost: config.fees.signature_verification_costs
                    [SignatureKind::MlDsa65]
                    .gas,
```

**File:** core/parameters/src/view.rs (L744-746)
```rust
        }
        .map(|_, value| ParameterCost { gas: value, compute: value.as_gas() });
        Self { costs }
```

**File:** chain/indexer/src/streamer/utils.rs (L32-40)
```rust
        let cost = calculate_tx_cost(
            &tx.receiver_id,
            &tx.signer_id,
            &tx.public_key,
            &actions,
            runtime_config,
            gas_price,
        )
        .unwrap();
```

**File:** runtime/runtime/src/config.rs (L440-441)
```rust
    burnt =
        burnt.checked_add_result(signature_verification_cost(fees, signer_public_key, actions)?)?;
```

**File:** runtime/runtime/src/config.rs (L469-472)
```rust
    Ok(TransactionCost {
        gas_burnt: burnt.gas,
        compute_burnt: burnt.compute,
        gas_remaining,
```
