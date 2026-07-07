### Title
Silent Fee Rate Truncation for Tiers ≥ 128 Due to `uint128` Shift Overflow in `nonDefaultFeeTierMask` — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary
In `OffchainExchange.updateTierFeeRates`, the expression `uint128(1) << txn.tier` silently evaluates to `0` whenever `txn.tier >= 128`, because Solidity 0.8.x returns `0` for shifts of a type by its own bit-width or more. As a result, `nonDefaultFeeTierMask` is never updated for those tiers, and `getTierFeeRateX18` always falls through to the hardcoded default rate for any user assigned a tier ≥ 128 — even though the custom rates were successfully written to `feeRates[tier][productId]`. This is a direct analog to the reported `uint256→uint32` truncation: a wider domain (`uint32` tier, up to ~4 billion) is silently capped by a narrower bitmask (`uint128`, 128 bits), causing stored configuration to be permanently unreachable.

---

### Finding Description

`OffchainExchange` uses a `uint128 nonDefaultFeeTierMask` to track which fee tiers have been customized. When a tier is configured, the mask is updated:

```solidity
// OffchainExchange.sol line 990
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

`txn.tier` is `uint32`. In Solidity 0.8.x, shifting a `uint128` value by 128 or more bits produces `0` — no revert, no warning. So for any `txn.tier >= 128`, the right-hand side evaluates to `0`, and the `|=` is a no-op. The custom rates are written to storage:

```solidity
feeRates[txn.tier][txn.productId] = FeeRates(txn.makerRateX18, txn.takerRateX18);
```

but the mask bit that would make them reachable is never set.

The lookup path in `getTierFeeRateX18` checks the mask before returning custom rates:

```solidity
// OffchainExchange.sol lines 933-946
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];
    }
    return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 });
}
```

Here `1` is a `uint256` literal, so `1 << tier` is a `uint256`. For `tier >= 128`, this produces a `uint256` with bit `tier` set — a value that can never appear in the 128-bit `nonDefaultFeeTierMask`. The condition is permanently false. Every user assigned a tier ≥ 128 receives the hardcoded default (0 maker, 2 bps taker), regardless of what was configured.

The type mismatch is structural: `txn.tier` is `uint32` (domain: 0 – 4,294,967,295), but the mask only covers tiers 0–127.

---

### Impact Explanation

**Impact: High**

Any fee tier ≥ 128 that is configured and assigned is silently dead. Concrete consequences:

- **Maker rebates lost**: If tier ≥ 128 carries a negative `makerRateX18` (rebate), market makers assigned to that tier receive `0` instead — a direct loss of funds owed to them.
- **Taker fee bypass**: If tier ≥ 128 carries a higher `takerRateX18` (e.g., for risk-tiered users), those users pay only 2 bps instead of the configured rate — protocol fee revenue is silently reduced.
- **Silent misconfiguration**: No revert, no event anomaly. The owner believes the tier is active; the sequencer assigns users to it; the fee engine ignores it entirely. The corrupted state is undetectable without off-chain reconciliation.

The corrupted invariant: `feeRates[tier][productId]` is non-zero and was intentionally set, but `getTierFeeRateX18(tier, productId)` always returns the default. The stored configuration and the applied configuration are permanently desynchronized.

---

### Likelihood Explanation

**Likelihood: Low**

The current deployment likely uses tiers 0–127 only. However:

- `txn.tier` is typed `uint32`, giving no compile-time or runtime guard against values ≥ 128.
- The protocol's tier system is designed to be extensible (the sequencer can assign any `uint32` tier via `UpdateFeeTier`).
- There is no on-chain assertion that `txn.tier < 128` in `updateTierFeeRates`.
- As the protocol scales its market-maker incentive or risk-tiering programs, tiers ≥ 128 become a natural expansion point — at which point the bug silently activates.

---

### Recommendation

1. Add an explicit bound check in `updateTierFeeRates`:
   ```solidity
   require(txn.tier < 128, "tier exceeds mask width");
   ```
2. Or expand `nonDefaultFeeTierMask` to `uint256` (covering tiers 0–255) and update the shift accordingly:
   ```solidity
   uint256 internal nonDefaultFeeTierMask;
   nonDefaultFeeTierMask |= uint256(1) << txn.tier; // safe for tier < 256
   ```
3. Add a matching bound check in `getTierFeeRateX18` or document the supported tier range explicitly.

---

### Proof of Concept

1. Owner submits `UpdateTierFeeRates` with `tier = 200`, `takerRateX18 = 50_000_000_000_000` (5 bps), for product 2.
2. `feeRates[200][2]` is written: `FeeRates(0, 50_000_000_000_000)`. ✓
3. `nonDefaultFeeTierMask |= uint128(1) << 200` → `uint128(1) << 200 == 0` → mask unchanged. ✗
4. Sequencer submits `UpdateFeeTier` assigning `feeTiers[alice] = 200`.
5. Alice places a taker order on product 2.
6. `getUserFeeRateWithBuilder` calls `getTierFeeRateX18(200, 2)`.
7. `nonDefaultFeeTierMask & (1 << 200)` → `nonDefaultFeeTierMask & (2^200)` → `0` (mask has no bits above 127).
8. Returns default: `FeeRates(0, 200_000_000_000_000)` — Alice pays 2 bps, not 5 bps.
9. Protocol loses 3 bps of fee revenue per fill; the configured rate is permanently unreachable. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/OffchainExchange.sol (L933-946)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
    }
```

**File:** core/contracts/OffchainExchange.sol (L962-991)
```text
    function updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
        external
        onlyEndpoint
    {
        if (txn.productId == QUOTE_PRODUCT_ID) {
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
            for (uint32 i = 0; i < spotProductIds.length; i++) {
                if (spotProductIds[i] == QUOTE_PRODUCT_ID) {
                    continue;
                }
                feeRates[txn.tier][spotProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
            for (uint32 i = 0; i < perpProductIds.length; i++) {
                feeRates[txn.tier][perpProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
    }
```
