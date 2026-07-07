### Title
Silent Bit-Shift Overflow in `nonDefaultFeeTierMask` for Tier ≥ 128 Causes Permanent Fee Miscollection — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`updateTierFeeRates` stores fee rates correctly in `feeRates[tier][productId]` for any `uint32` tier value, but the companion mask update `nonDefaultFeeTierMask |= uint128(1) << txn.tier` silently produces `0` for any `tier >= 128` in Solidity 0.8. As a result, `getTierFeeRateX18` always falls through to the hardcoded 2 bps default for those tiers, making the configured rates permanently unreachable.

---

### Finding Description

**Write side — `updateTierFeeRates` (line 990):**

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

`nonDefaultFeeTierMask` is `uint128` (128-bit wide). `txn.tier` is `uint32`. In Solidity ≥ 0.8, a shift of a value by an amount ≥ its bit-width returns `0` without reverting. So for any `txn.tier >= 128`, `uint128(1) << txn.tier == 0`, and the OR-assignment is a no-op — the bit for that tier is never set. [1](#0-0) 

**Read side — `getTierFeeRateX18` (line 938):**

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
```

The untyped literal `1` is `uint256` in this expression context. For `tier >= 128`, `uint256(1) << tier` sets a bit beyond position 127. When ANDed with the `uint128` mask (which only has bits 0–127), the result is always `0`. The configured rates in `feeRates[tier][productId]` are never returned. [2](#0-1) 

**No tier-range validation exists anywhere in the call chain.** `updateFeeTier` accepts any `uint32 newTier` without bounds checking, so users can legitimately be assigned tiers ≥ 128. [3](#0-2) 

The owner-facing entry point in `ContractOwner.updateTierFeeRates` also accepts arbitrary `uint32[] tier` values and submits them as slow-mode transactions with no upper-bound guard. [4](#0-3) 

---

### Impact Explanation

Any user assigned to a tier ≥ 128 always pays the hardcoded 2 bps taker rate and 0 maker rate, regardless of what was configured. If the intended rate is lower (e.g., 0 bps for a market-maker rebate program), the protocol over-collects fees from those users. If the intended rate is higher, the protocol under-collects. In both cases the `collectedFees` accounting in `MarketInfoStore` diverges from what was contractually agreed, constituting a direct fee-accounting error. [5](#0-4) 

---

### Likelihood Explanation

The `tier` field is typed `uint32` throughout the ABI and no documentation or on-chain guard caps it at 127. A protocol operator assigning tier IDs ≥ 128 to high-volume or VIP traders — a natural extension of a tiered fee system — would trigger this silently. The misconfiguration produces no revert, no event anomaly, and no observable error at the time of the `updateTierFeeRates` call; it only manifests at trade time when `getTierFeeRateX18` is queried. [6](#0-5) 

---

### Recommendation

Two complementary fixes are needed:

1. **Enforce the tier range.** Add a `require(txn.tier < 128, "tier out of range")` guard at the top of `updateTierFeeRates` (and symmetrically in `updateFeeTier`) so that tiers ≥ 128 are rejected at the point of configuration rather than silently misbehaving.

2. **Widen the mask, or use a mapping.** If tiers ≥ 128 are a desired feature, replace `uint128 nonDefaultFeeTierMask` with `uint256` (supporting up to 256 tiers) or replace the bitmask entirely with a `mapping(uint32 => bool) isNonDefaultTier` to remove the width constraint altogether.

---

### Proof of Concept

```solidity
// Hardhat/Foundry unit test (unmodified contract)
function testTierOverflow() public {
    uint32 tier = 200;
    uint32 productId = 1;
    int128 makerRate = 0;
    int128 takerRate = 0; // zero-fee tier for VIP

    // Owner submits; sequencer processes via endpoint
    offchainExchange.updateTierFeeRates(
        IEndpoint.UpdateTierFeeRates(tier, productId, makerRate, takerRate)
    );

    // feeRates[200][1] is correctly stored
    // but nonDefaultFeeTierMask bit 200 was never set (silent 0)

    IOffchainExchange.FeeRates memory rates =
        offchainExchange.getTierFeeRateX18(tier, productId);

    // FAILS: returns 2bps default instead of 0
    assertEq(rates.takerRateX18, 0);
    // Actual: rates.takerRateX18 == 200_000_000_000_000 (2 bps)
}
```

The root cause is the two-line interaction:
- Line 990: `nonDefaultFeeTierMask |= uint128(1) << txn.tier;` → `0` for tier ≥ 128
- Line 938: `nonDefaultFeeTierMask & (1 << tier) != 0` → always `false` for tier ≥ 128 [7](#0-6) [1](#0-0)

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

**File:** core/contracts/OffchainExchange.sol (L952-959)
```text
    function updateFeeTier(address user, uint32 newTier) external {
        require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
        if (newTier != 0 && !addressTouched[user]) {
            addressTouched[user] = true;
            customFeeAddresses.push(user);
        }
        feeTiers[user] = newTier;
        emit FeeTierUpdate(user, newTier);
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

**File:** core/contracts/ContractOwner.sol (L405-431)
```text
    function updateTierFeeRates(
        uint32[] memory tier,
        uint32[] memory productId,
        int128[] memory makerRateX18,
        int128[] memory takerRateX18
    ) external onlyOwner {
        if (
            tier.length != productId.length ||
            tier.length != makerRateX18.length ||
            tier.length != takerRateX18.length
        ) {
            revert InvalidInput();
        }
        for (uint256 i = 0; i < tier.length; i++) {
            IEndpoint.UpdateTierFeeRates memory _txn = IEndpoint
                .UpdateTierFeeRates(
                    tier[i],
                    productId[i],
                    makerRateX18[i],
                    takerRateX18[i]
                );
            _submitSlowModeTransaction(
                IEndpoint.TransactionType.UpdateTierFeeRates,
                abi.encode(_txn)
            );
        }
    }
```
