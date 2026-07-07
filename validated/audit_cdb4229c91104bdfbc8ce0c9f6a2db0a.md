### Title
Finalization of Underwater Subaccounts Provides No Incentive to External Liquidators — (`core/contracts/EndpointTx.sol`, `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

The Nado protocol's two-phase liquidation process requires a final "finalization" call (`productId == type(uint32).max`) to fully resolve an underwater subaccount. The protocol's own code comment explicitly acknowledges that external liquidators receive no profit from performing finalization. Because finalization imposes a net cost on the caller (positive PnL must be paid out to the liquidatee, plus gas), no rational external liquidator will perform it. The protocol relies entirely on the sequencer (`N_ACCOUNT`) to finalize accounts, creating a single point of failure with no fallback incentive mechanism.

---

### Finding Description

Liquidation in Nado is a two-phase process:

**Phase 1 — Position liquidation** (`productId != type(uint32).max`): The liquidator acquires the liquidatee's positions at a discount. The discount is split: 50% goes to the insurance fund as `liquidationFees`, and 50% is retained by the liquidator as profit. The liquidator also pays a flat `LIQUIDATION_FEE` of $1. [1](#0-0) [2](#0-1) 

**Phase 2 — Finalization** (`productId == type(uint32).max`): After all positions are closed, a caller must invoke `_finalizeSubaccount` to settle remaining PnL, apply insurance coverage, and socialize losses. The code comment in `EndpointTx.sol` explicitly states:

> "No liquidation fee for finalization (productId == uint32.max) because: 1) The liquidator receives no profit from finalization" [3](#0-2) 

Inside `_finalizeSubaccount`, the caller is required to settle **positive PnL** of the liquidatee via `_settlePnlAgainstLiquidator`. When `pnl > 0`, the liquidator's quote balance is debited by `pnl` and the liquidatee's quote balance is credited: [4](#0-3) 

This means the caller of finalization:
1. Pays out the liquidatee's positive PnL from their own quote balance (a direct cost).
2. Receives back only the liquidatee's negative PnL (capped by the liquidatee's remaining quote balance).
3. Receives no discount, no fee share, and no compensation from the insurance fund.
4. Still incurs gas costs.

The net economic outcome for an external liquidator performing finalization is zero or negative. The `N_ACCOUNT` (sequencer) bypass at line 396 is the only path that avoids this cost, confirming the design relies on the sequencer to perform finalization: [5](#0-4) 

The `_finalizeSubaccount` function is the only mechanism to trigger `perpEngine.socializeSubaccount` and `spotEngine.socializeSubaccount`, which are required to fully resolve an insolvent account: [6](#0-5) 

---

### Impact Explanation

If the sequencer (`N_ACCOUNT`) fails to submit finalization transactions — whether due to downtime, censorship of a specific account, or a bug — no external liquidator has any economic reason to step in. The consequences are:

- Underwater subaccounts remain in a permanently half-liquidated state: all positions are closed but the quote deficit is never socialized.
- `perpEngine.socializeSubaccount` is never called, meaning the negative PnL is never distributed across the protocol's counterparties. Other users' unrealized gains remain artificially inflated.
- The insurance fund's `lastLiquidationFees` reservation (line 369) is never released, permanently reducing the effective insurance available to other liquidations. [7](#0-6) 

---

### Likelihood Explanation

The sequencer is a centralized off-chain component. Any period of sequencer unavailability, or deliberate sequencer censorship of a specific liquidatee, leaves finalization unperformed with no on-chain fallback. The slow-mode escape hatch (`submitSlowModeTransactionImpl`) does not handle `LiquidateSubaccount` transactions, so users cannot bypass the sequencer for finalization. [8](#0-7) 

---

### Recommendation

Provide a concrete economic incentive for external callers to perform finalization. One approach: allocate a fixed percentage of the insurance fund (analogous to the `LIQUIDATION_FEE_FRACTION` used in Phase 1) as a bounty paid to the `txn.sender` upon successful finalization. This mirrors the recommendation in M-15 to always give a percentage of the liquidated account's collateral as a fee to the liquidator. Alternatively, allow finalization to be submitted via slow mode so that the sequencer's availability is not a prerequisite.

---

### Proof of Concept

1. Account `A` becomes undercollateralized. A liquidator performs Phase 1 liquidations, closing all of `A`'s positions. The liquidator profits from the discount.
2. `A` now has zero positions but a negative quote balance (e.g., `-$500`). Finalization is required to call `socializeSubaccount`.
3. The sequencer goes offline (or censors `A`'s finalization).
4. No external liquidator submits finalization because:
   - `A` has positive PnL in some perps (`vQuoteBalance > 0`). The caller of finalization must pay this out of their own quote balance (e.g., `-$200`).
   - The caller receives back only the negative PnL capped by `A`'s remaining quote (e.g., `+$0` since `A`'s quote is already negative).
   - Net: the caller loses `$200` plus gas.
5. `A`'s `-$500` quote deficit is never socialized. Other users' gains remain inflated. The `lastLiquidationFees` reservation is permanently locked, reducing available insurance for all future liquidations. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L266-269)
```text
        perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);
        perpEngine.updateBalance(perpId, txn.sender, 0, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);
```

**File:** core/contracts/ClearinghouseLiq.sol (L279-286)
```text
    function _finalizeSubaccount(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal returns (bool) {
        if (txn.productId != type(uint32).max) {
            return false;
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L322-338)
```text
        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }
```

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

**File:** core/contracts/ClearinghouseLiq.sol (L550-552)
```text
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
```

**File:** core/contracts/common/Constants.sol (L27-36)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1

uint128 constant INT128_MAX = uint128(type(int128).max);

uint64 constant SECONDS_PER_DAY = 3600 * 24;

uint32 constant VRTX_PRODUCT_ID = 41;

int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%
```

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L396-412)
```text
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
                // No liquidation fee for finalization (productId == uint32.max) because:
                // 1) The liquidator receives no profit from finalization
                // 2) Finalization can only occur once per underwater subaccount, eliminating
                //    sybil attack concerns that would otherwise require a fee deterrent.
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
```
