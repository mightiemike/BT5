### Title
Overly Strict Access Control on `DumpFees` Slow Mode Transaction Locks Protocol Fees — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `DumpFees` slow mode transaction, which distributes all accumulated trading fees and sequencer fees to the fixed `X_ACCOUNT`, is restricted exclusively to the protocol owner. Since the destination is a fixed address, this restriction is unnecessary and creates a single point of failure: if the owner becomes permanently unavailable, all accumulated fees are locked with no alternative recovery path.

---

### Finding Description

In `EndpointTx.submitSlowModeTransactionImpl`, a group of admin-only transaction types is gated behind `require(sender == owner())`: [1](#0-0) 

`DumpFees` is explicitly included in this group. When processed, it executes two fee-distribution steps:

1. `IOffchainExchange(offchainExchange).dumpFees()` — iterates over all spot and perp product IDs and transfers the entire `marketInfo[productId].collectedFees` balance to `X_ACCOUNT`: [2](#0-1) 

2. `clearinghouse.claimSequencerFees(fees)` — transfers all `sequencerFee[productId]` balances to `X_ACCOUNT`: [3](#0-2) 

Both destinations are hardcoded to `X_ACCOUNT`. There is no alternative code path to distribute these fees. The `ContractOwner.dumpFees()` wrapper also enforces `onlyOwner`, confirming the single point of failure: [4](#0-3) 

Trading fees accumulate continuously via `updateCollectedFees` on every matched order: [5](#0-4) 

Sequencer fees accumulate via `chargeFee` on every sequenced transaction (withdrawals, liquidations, NLP operations): [6](#0-5) 

Neither `OffchainExchange.dumpFees()` (guarded `onlyEndpoint`) nor `Clearinghouse.claimSequencerFees()` (guarded `onlyEndpoint`) can be called by any path other than the sequencer processing a `DumpFees` slow mode transaction submitted by the owner. [7](#0-6) 

---

### Impact Explanation

If the owner (multisig) becomes permanently unavailable — through key loss, governance failure, or contract upgrade issues — all accumulated `marketInfo[productId].collectedFees` (trading fees) and `sequencerFee[productId]` (sequencer fees) are permanently locked inside the engine contracts and can never reach `X_ACCOUNT`. The `X_ACCOUNT` is the sequencer's operational account used for rebalancing and protocol operations; starvation of this account degrades protocol liveness. The corrupted state is the permanent non-zero balance of `collectedFees` and `sequencerFee` with no recovery path.

---

### Likelihood Explanation

Medium-Low. The owner is a multisig, which is more resilient than a single EOA. However, the vulnerability class is identical to the reference bug: a fee-distribution function that transfers to a fixed address is unnecessarily restricted to a single privileged actor. Multisig key loss, governance deadlock, or a failed upgrade that bricks the owner contract are all realistic worst-case scenarios. Fees accumulate on every trade, so the locked amount grows continuously.

---

### Recommendation

Remove `DumpFees` from the `require(sender == owner())` group in `EndpointTx.submitSlowModeTransactionImpl`. Since both `OffchainExchange.dumpFees()` and `Clearinghouse.claimSequencerFees()` transfer exclusively to the fixed `X_ACCOUNT`, any caller should be permitted to submit the `DumpFees` slow mode transaction. This mirrors the fix applied in the reference audit (PR 315): when the destination is fixed, the trigger should be permissionless.

---

### Proof of Concept

1. Trader A and Trader B match orders on any product. `updateCollectedFees` increments `marketInfo[productId].collectedFees` on every fill.
2. Users submit withdrawals and liquidations. `chargeFee` increments `sequencerFee[productId]` on every sequenced transaction.
3. The owner multisig loses quorum (e.g., 3-of-5 keys lost).
4. No one can call `ContractOwner.dumpFees()` (blocked by `onlyOwner`) or submit a `DumpFees` slow mode transaction directly to the Endpoint (blocked by `require(sender == owner())`).
5. `OffchainExchange.dumpFees()` and `Clearinghouse.claimSequencerFees()` are both `onlyEndpoint` and have no alternative call path.
6. All accumulated `collectedFees` and `sequencerFee` balances remain permanently locked; `X_ACCOUNT` receives no further fee distributions.

### Citations

**File:** core/contracts/EndpointTx.sol (L130-141)
```text
    function chargeFee(bytes32 sender, int128 fee) internal {
        chargeFee(sender, fee, QUOTE_PRODUCT_ID);
    }

    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
```

**File:** core/contracts/EndpointTx.sol (L355-368)
```text
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
```

**File:** core/contracts/OffchainExchange.sol (L601-609)
```text
    function updateCollectedFees(
        uint32, /* productId */
        MarketInfo memory market,
        bool, /* taker */
        int128 fee,
        int128 /* builder fee */
    ) internal virtual {
        market.collectedFees += fee;
    }
```

**File:** core/contracts/OffchainExchange.sol (L891-931)
```text
    function dumpFees() external onlyEndpoint {
        // loop over all spot and perp product ids
        uint32[] memory productIds = spotEngine.getProductIds();

        for (uint32 i = 1; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            spotEngine.updateBalance(
                quoteIds[productId],
                X_ACCOUNT,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }

        productIds = perpEngine.getProductIds();

        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            perpEngine.updateBalance(
                productId,
                X_ACCOUNT,
                0,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L569-615)
```text
    function claimSequencerFees(int128[] calldata fees)
        external
        virtual
        onlyEndpoint
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        uint32[] memory spotIds = spotEngine.getProductIds();
        uint32[] memory perpIds = perpEngine.getProductIds();

        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
        }

        for (uint256 i = 0; i < perpIds.length; i++) {
            IPerpEngine.Balance memory feeBalance = perpEngine.getBalance(
                perpIds[i],
                FEES_ACCOUNT
            );
            perpEngine.updateBalance(
                perpIds[i],
                X_ACCOUNT,
                feeBalance.amount,
                feeBalance.vQuoteBalance
            );
            perpEngine.updateBalance(
                perpIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount,
                -feeBalance.vQuoteBalance
            );
        }
    }
```

**File:** core/contracts/ContractOwner.sol (L382-387)
```text
    function dumpFees() external onlyOwner {
        bytes memory txn = abi.encodePacked(
            uint8(IEndpoint.TransactionType.DumpFees)
        );
        endpoint.submitSlowModeTransaction(txn);
    }
```
