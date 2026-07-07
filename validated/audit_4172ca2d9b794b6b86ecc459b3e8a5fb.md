Looking at the actual code in `ClearinghouseLiq.sol` and `ClearinghouseStorage.sol`, I need to trace the exact storage mutation and determine whether the invariant can break.

**Key code paths:**

`_handleLiquidationPayment` (non-finalization step): [1](#0-0) 

`_finalizeSubaccount` (finalization step): [2](#0-1) [3](#0-2) 

`lastLiquidationFees` storage declaration: [4](#0-3) 

---

### Analysis

**The design intent** (comment at lines 582–586) is that `lastLiquidationFees` holds the fees from the *immediately preceding* non-finalization liquidation step for the *same* subaccount, so those fees are excluded from the insurance used for socialization (to avoid the fees "blocking" socialization). At the end of `_finalizeSubaccount`, the fees are added back before writing `insurance` to storage.

**The bug:** `lastLiquidationFees` is a **global** storage variable. It is:
- Written in `_handleLiquidationPayment` (line 586)
- Read but **never reset** in `_finalizeSubaccount`
- Never zeroed between different subaccount liquidation sequences

This means if subaccount A's non-finalization step runs last (setting `lastLiquidationFees = X`), and then subaccount B's finalization runs, B's finalization uses the stale `X`.

**Concrete impact scenario:**

| Variable | Value |
|---|---|
| `insurance` | 600 |
| `lastLiquidationFees` (stale, from A) | 100 |
| `quoteBalance.amount` for B | -500 |

**Without stale fees:** `v.insurance = 600`, `insuranceCover = min(600, 500) = 500`, `v.insurance = 100 > 0` → no spot socialization. `insurance = 100`.

**With stale fees:** `v.insurance = 600 - 100 = 500`, `insuranceCover = min(500, 500) = 500`, `v.insurance = 0 ≤ 0` → **`spotEngine.socializeSubaccount` is triggered**, spreading losses to all depositors. Then `v.insurance += 100`, `insurance = 100`.

The insurance fund ends up at the same level (100), but depositors have been incorrectly socialized for losses the insurance fund could have fully covered.

**Worse case** (insurance insufficient even without stale fees):

| Variable | Value |
|---|---|
| `insurance` | 600 |
| `lastLiquidationFees` (stale) | 100 |
| `quoteBalance.amount` for B | -700 |

Without stale: `insuranceCover = 600`, socializes 100 of losses.
With stale: `v.insurance = 500`, `insuranceCover = 500`, socializes **200** of losses — 100 more than necessary, while the insurance fund retains the same 100 at the end.

**Is this reachable?**

`liquidateSubaccountImpl` is called via `delegatecall` from `Clearinghouse.liquidateSubaccount`: [5](#0-4) 

The `onlyEndpoint` gate means the sequencer processes these. A liquidator submits signed `LiquidateSubaccount` transactions to the sequencer. The sequencer processes them in submission order. The liquidator can submit:
1. A non-finalization liquidation of subaccount A (large fees → `lastLiquidationFees = X`)
2. A finalization of subaccount B (uses stale `X`)

No sequencer compromise is needed — the liquidator controls the *content and order* of their submitted transactions. The sequencer processes them as submitted.

**The invariant that breaks:** `insurance` written to storage at line 411 is arithmetically correct (fees are restored), but `perpEngine.socializeSubaccount` and `spotEngine.socializeSubaccount` are called with an artificially deflated `v.insurance`, causing more losses to be socialized than the insurance fund actually requires.

---

### Title
Stale `lastLiquidationFees` in `_finalizeSubaccount` causes premature socialization, spreading losses to depositors when insurance could cover them — (`core/contracts/ClearinghouseLiq.sol`)

### Summary
`lastLiquidationFees` is a global storage variable that is never reset between different subaccount liquidation sequences. When `_finalizeSubaccount` runs for subaccount B after a non-finalization step for subaccount A, it uses A's stale fees to reduce the effective insurance passed to `perpEngine.socializeSubaccount` and the `v.insurance <= 0` check for `spotEngine.socializeSubaccount`, causing more losses to be socialized to depositors than necessary.

### Finding Description
In `_finalizeSubaccount`:

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;   // uses stale global value
```

`lastLiquidationFees` is set in `_handleLiquidationPayment` (line 586) and is never reset to zero between different subaccount liquidations. The finalization of subaccount B will subtract fees that were accrued during subaccount A's liquidation, artificially deflating `v.insurance` for the entire socialization computation. Although `v.insurance += lastLiquidationFees` at line 410 restores the value before writing to `insurance` storage, the intermediate socialization calls (`perpEngine.socializeSubaccount` at line 386 and `spotEngine.socializeSubaccount` at line 408) have already executed with the deflated value.

### Impact Explanation
Depositors (NLP holders) bear losses through socialization that the insurance fund could have covered. The insurance fund balance written to storage is arithmetically correct, but the distribution of losses between the insurance fund and depositors is wrong — depositors absorb more bad debt than they should.

### Likelihood Explanation
Any liquidator can trigger this by submitting a non-finalization liquidation of one subaccount followed by a finalization of another subaccount. Both subaccounts must be under maintenance health, which is a normal market condition. No sequencer compromise is required.

### Recommendation
Reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount`, or pass it as a parameter scoped to the current liquidation sequence rather than storing it globally. Alternatively, only subtract `lastLiquidationFees` if the finalization is for the same subaccount as the last non-finalization step (track the liquidatee alongside the fees).

### Proof of Concept
1. Deploy protocol. Set `insurance = 600`.
2. Liquidator submits non-finalization liquidation of subaccount A → `_handleLiquidationPayment` sets `lastLiquidationFees = 100`, `insurance = 700`.
3. Liquidator submits finalization of subaccount B (all positions closed, `quoteBalance = -500`).
4. In `_finalizeSubaccount`: `v.insurance = 700 - 100 = 600`. After covering 500 of bad debt: `v.insurance = 100`. But if `insurance` were 600 (without A's fees having been added), `v.insurance = 600 - 100 = 500`, `insuranceCover = 500`, `v.insurance = 0` → `spotEngine.socializeSubaccount` fires.
5. Assert: socialization occurred despite `insurance` being sufficient to cover all of B's bad debt without socializing.

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L368-370)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-411)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```
