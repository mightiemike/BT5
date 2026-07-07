The mathematical invariant violation is real and provable. Let me lay out the full analysis.

---

### Title
Unbacked Collateral Creation via `minDepositRateX18` Growing Net Liquidity Without Token Inflow — (`core/contracts/SpotEngineState.sol`)

---

### Summary

The `minDepositRate` block in `_updateState` multiplies **both** `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` by `minDepositRateMultiplierX18`. This scales net liquidity (`totalDeposits − totalBorrows`) by that multiplier each tick, creating unbacked withdrawable collateral with no corresponding token inflow into Clearinghouse.

---

### Finding Description

The code's own invariant comment states:

> "if we don't take fees into account, the liquidity, which is (deposits - borrows) should remain the same after updating state." [1](#0-0) 

The normal interest block preserves this invariant by design: `depositRateMultiplier = utilization × (borrowRateMultiplier − 1) + 1`, so `td·cdm·drm − tb·cbm·brm = td·cdm − tb·cbm`.

Then the `minDepositRate` block applies:

```
cdm' = cdm × M
cbm' = cbm × M
``` [2](#0-1) 

where `M = (1 + minDepositRatePerSecond)^dt > 1`.

**Net liquidity after this block:**

```
td × cdm × M  −  tb × cbm × M
= M × (td × cdm − tb × cbm)
= M × L
```

The change per tick is `(M − 1) × L`. This extra liquidity has no corresponding token inflow. The tokens held by Clearinghouse are unchanged; only the accounting multipliers grow.

No insurance fund debit, no protocol treasury transfer, and no external token pull occurs anywhere in this code path to fund the gap. [3](#0-2) 

---

### Impact Explanation

After N ticks, total withdrawable collateral for depositors = `totalDepositsNormalized × cdm × M^N`. Tokens held by Clearinghouse = original deposits (net of borrows/repayments). When utilization < 100% (the normal case), the free liquidity `L = totalDeposits − totalBorrows` grows by `(M−1)×L` per tick with no backing. Depositors can collectively withdraw more tokens than were ever deposited, draining Clearinghouse of tokens belonging to other products or the insurance fund.

At utilization = 0 (no borrowers at all), the entire deposit base inflates by `M` per tick with zero funding source.

---

### Likelihood Explanation

`minDepositRateX18` is a standard per-product config field set by the owner via `addOrUpdateProduct`. Its presence in the ABI and config struct indicates it is an intended production feature, not a dead code path. [4](#0-3) 

SpotTick is processed by the sequencer through `submitTransactionsChecked` as part of normal protocol operation — no sequencer compromise is required. The sequencer processes SpotTick honestly and the invariant violation accumulates automatically. [5](#0-4) 

The sequencer check at `submitTransactionsChecked` only gates who submits the batch; it does not prevent the accounting error from occurring during normal operation. [6](#0-5) 

---

### Recommendation

The `minDepositRate` subsidy must be funded. Two correct approaches:

1. **Insurance-fund-backed:** Only multiply `cumulativeDepositsMultiplierX18` by `M`. Compute the gap `totalDeposits × (M − 1)` and debit it from the insurance fund (or a dedicated protocol reserve). If the insurance fund is insufficient, cap the effective `minDepositRate` to what can be funded.

2. **Borrower-only funding (correct intent):** Do not apply `M` to `cumulativeBorrowsMultiplierX18` at all. The minDepositRate subsidy should come entirely from borrowers paying a higher rate, but only up to the point where `totalBorrows × (brm − 1) ≥ totalDeposits × (M − 1)`. When utilization is too low to fund the minimum, the gap must come from the insurance fund.

The current code applies `M` to both multipliers, which funds the depositor subsidy from thin air rather than from borrowers or reserves.

---

### Proof of Concept

```solidity
// Setup: product with minDepositRateX18 = 0.05e18 (5% APY), zero borrows
// Alice deposits 1000 tokens. Clearinghouse holds 1000 tokens.

// After 1 year of SpotTick processing (normal sequencer operation):
// M = (1 + 0.05/31536000)^31536000 ≈ 1.05127
// Alice's withdrawable = 1000 × 1.05127 = 1051.27 tokens
// Clearinghouse still holds 1000 tokens
// Unbacked amount = 51.27 tokens

// Fuzz assertion (should hold but doesn't):
// assert(clearinghouse.balanceOf(token) >= sum(withdrawable_balances))
// FAILS by (M^N - 1) × L after N ticks
```

The exact storage delta: `states[productId].cumulativeDepositsMultiplierX18` grows by factor `M` per tick with no corresponding increase in `IERC20Base(token).balanceOf(address(clearinghouse))`. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/SpotEngineState.sol (L102-113)
```text
        // if we don't take fees into account, the liquidity, which is
        // (deposits - borrows) should remain the same after updating state.

        // For simplicity, we use `tb`, `cbm`, `td`, and `cdm` for
        // `totalBorrowsNormalized`, `cumulativeBorrowsMultiplier`,
        // `totalDepositsNormalized`, and `cumulativeDepositsMultiplier`

        // before the updating, the liquidity is (td * cdm - tb * cbm)
        // after the updating, the liquidity is
        // (td * cdm * depositRateMultiplier - tb * cbm * borrowRateMultiplier)
        // so we can get
        // depositRateMultiplier = utilization * (borrowRateMultiplier - 1) + 1
```

**File:** core/contracts/SpotEngineState.sol (L147-169)
```text
        // apply the min deposit rate
        if (minDepositRateX18 != 0) {
            int128 minDepositRatePerSecondX18 = minDepositRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            int128 minDepositRateMultiplierX18 = (ONE +
                minDepositRatePerSecondX18).pow(int128(dt));

            state.cumulativeBorrowsMultiplierX18 = state
                .cumulativeBorrowsMultiplierX18
                .mul(minDepositRateMultiplierX18);

            state.cumulativeDepositsMultiplierX18 = state
                .cumulativeDepositsMultiplierX18
                .mul(minDepositRateMultiplierX18);

            depositRateMultiplierX18 = depositRateMultiplierX18.mul(
                minDepositRateMultiplierX18
            );
            borrowRateMultiplierX18 = borrowRateMultiplierX18.mul(
                minDepositRateMultiplierX18
            );
        }
```

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L23-31)
```text
    struct Config {
        address token;
        int128 interestInflectionUtilX18;
        int128 interestFloorX18;
        int128 interestSmallCapX18;
        int128 interestLargeCapX18;
        int128 withdrawFeeX18;
        int128 minDepositRateX18;
    }
```

**File:** core/contracts/EndpointTx.sol (L466-475)
```text
        } else if (txType == IEndpoint.TransactionType.SpotTick) {
            IEndpoint.SpotTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.SpotTick)
            );
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L387-389)
```text
    function _balanceOf(address token) internal view virtual returns (uint128) {
        return uint128(IERC20Base(token).balanceOf(address(this)));
    }
```
