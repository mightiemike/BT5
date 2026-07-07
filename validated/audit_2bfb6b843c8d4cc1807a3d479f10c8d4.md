### Title
Missing Isolated Subaccount Type Check in `liquidateSubaccountImpl` Allows Regular Liquidation of Isolated Subaccounts — (`File: core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`liquidateSubaccountImpl` validates that the **sender** is not an isolated subaccount but never validates that the **liquidatee** is not an isolated subaccount when `productId != type(uint32).max`. An isolated subaccount that goes underwater can be partially liquidated via the regular cross-margin liquidation path, bypassing the dedicated `tryCloseIsolatedSubaccount` closure mechanism and leaving the isolated subaccount in a permanently inconsistent state.

---

### Finding Description

`liquidateSubaccountImpl` in `ClearinghouseLiq.sol` enforces one side of the subaccount-type invariant:

```solidity
require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
``` [1](#0-0) 

The function then branches on `productId`:

- If `productId == type(uint32).max` (finalize path), `_finalizeSubaccount` is called, which **does** handle isolated liquidatees by calling `tryCloseIsolatedSubaccount`.
- For **every other `productId`** (regular liquidation path), `_finalizeSubaccount` returns `false` immediately and the code falls through to `_handleLiquidationPayment` — with **no check** that the liquidatee is not an isolated subaccount. [2](#0-1) 

The finalize path's isolated-subaccount handling is:

```solidity
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
        IOffchainExchange(...).tryCloseIsolatedSubaccount(txn.liquidatee);
    }
    return;
}
``` [3](#0-2) 

`_finalizeSubaccount` exits immediately when `productId != type(uint32).max`:

```solidity
function _finalizeSubaccount(...) internal returns (bool) {
    if (txn.productId != type(uint32).max) {
        return false;
    }
    ...
}
``` [4](#0-3) 

So for any non-max `productId`, the isolated-subaccount branch is never reached, and `_handleLiquidationPayment` executes unconditionally, performing raw `updateBalance` calls on the isolated subaccount and the liquidator's cross-margin account. [5](#0-4) 

The `isIsolatedSubaccount` sentinel is a pure bit-pattern check:

```solidity
function isIsolatedSubaccount(bytes32 subaccount) internal pure returns (bool) {
    return uint256(subaccount) & 0xFFFFFF == 6910831;
}
``` [6](#0-5) 

---

### Impact Explanation

When a liquidator submits a `LiquidateSubaccount` transaction with an isolated subaccount as `liquidatee` and a concrete `productId` (not `uint32.max`):

1. The isolated subaccount's position is partially reduced via `updateBalance` without calling `tryCloseIsolatedSubaccount`.
2. The liquidator's **cross-margin** account receives the transferred position — a position that was supposed to remain isolated.
3. The isolated subaccount remains registered in `isolatedSubaccounts` / `isolatedSubaccountsMask` as active, but its on-chain balance state is now inconsistent with the registration.
4. The parent subaccount's margin that was locked into the isolated subaccount is not properly returned, and the isolated slot is never freed.

This corrupts both the isolated subaccount's accounting and the liquidator's cross-margin health, and permanently occupies an isolated subaccount slot for the parent address. [7](#0-6) 

---

### Likelihood Explanation

The entry path is fully unprivileged. Any address can submit a `LiquidateSubaccount` transaction via the sequencer fast path or the slow-mode path. The only precondition is that the target isolated subaccount is `isUnderMaintenance`, which is a normal market condition for any leveraged position. The liquidator has a direct financial incentive (liquidation fees) to trigger this path. [8](#0-7) 

---

### Recommendation

Add an explicit guard at the top of `liquidateSubaccountImpl` (or immediately after the finalize branch) to reject regular liquidation of isolated subaccounts:

```solidity
if (!_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    require(
        !RiskHelper.isIsolatedSubaccount(txn.liquidatee),
        ERR_UNAUTHORIZED
    );
    ...
}
```

Isolated subaccounts that go underwater must be closed exclusively through the finalize path (`productId == type(uint32).max`) which correctly calls `tryCloseIsolatedSubaccount`. [9](#0-8) 

---

### Proof of Concept

1. Trader opens an isolated subaccount for product `P` via `CreateIsolatedSubaccount`. The isolated subaccount `iso` is registered in `isolatedSubaccounts[parent][id]`.
2. The position moves against the trader; `isUnderMaintenance(iso)` returns `true`.
3. Attacker (liquidator) submits `LiquidateSubaccount { sender: attacker, liquidatee: iso, productId: P, amount: X }` with `productId != type(uint32).max`.
4. `liquidateSubaccountImpl` passes all guards (sender is not isolated, liquidatee is not `X_ACCOUNT`/`N_ACCOUNT`, product is not quote).
5. `_finalizeSubaccount` returns `false` immediately because `productId != uint32.max`.
6. `_handleLiquidationPayment` executes: transfers position from `iso` to `attacker`'s cross-margin account, charges liquidation fees.
7. `tryCloseIsolatedSubaccount` is **never called**; `iso` remains in `isolatedSubaccounts` as active with a now-corrupted balance, and the parent's locked margin is not returned. [4](#0-3) [2](#0-1)

### Citations

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

**File:** core/contracts/ClearinghouseLiq.sol (L541-570)
```text
                    insurance
                );
            }
        } else {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );
            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
            perpEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount,
                v.liquidationPayment
            );
            perpEngine.updateBalance(
                txn.productId,
                txn.sender,
                txn.amount,
                -v.liquidationPayment
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationFees
            );
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
    }
```

**File:** core/contracts/libraries/RiskHelper.sol (L83-89)
```text
    function isIsolatedSubaccount(bytes32 subaccount)
        internal
        pure
        returns (bool)
    {
        return uint256(subaccount) & 0xFFFFFF == 6910831;
    }
```

**File:** core/contracts/OffchainExchange.sol (L1055-1065)
```text
            // |  address | reserved | productId |   id   |  'iso'  |
            // | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
            newIsolatedSubaccount = bytes32(
                (uint256(uint160(senderAddress)) << 96) |
                    (uint256(txn.productId) << 32) |
                    (uint256(id) << 24) |
                    6910831
            );
            isolatedSubaccountsMask[senderAddress] |= 1 << id;
            parentSubaccounts[newIsolatedSubaccount] = txn.order.sender;
            isolatedSubaccounts[txn.order.sender][id] = newIsolatedSubaccount;
```

**File:** core/contracts/EndpointTx.sol (L391-412)
```text
        if (txType == IEndpoint.TransactionType.LiquidateSubaccount) {
            IEndpoint.SignedLiquidateSubaccount memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLiquidateSubaccount)
            );
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
