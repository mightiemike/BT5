### Title
NLP Holder Funds Permanently Locked on Sequencer Failure — No Slow-Mode `BurnNlp` Escape Hatch and Oracle Time Frozen - (File: `core/contracts/EndpointTx.sol`, `core/contracts/SpotEngineState.sol`)

---

### Summary

When a user mints NLP tokens, their NLP balance is locked for `NLP_LOCK_PERIOD` (4 days) and can only be redeemed via a `BurnNlp` transaction. `BurnNlp` is exclusively processed through the sequencer-gated path (`processTransactionImpl`). The slow-mode escape hatch (`processSlowModeTransactionImpl`) does not include `BurnNlp`. Additionally, the unlock condition for the NLP locked-balance queue depends on `getOracleTime()`, which is itself only advanced by sequencer-submitted `SpotTick`/`PerpTick` transactions. If the sequencer fails permanently, both the time-based unlock and the burn redemption path are simultaneously frozen, permanently locking NLP holders' underlying quote collateral with no permissionless recovery path.

---

### Finding Description

**Layer 1 — Oracle time freeze locks NLP balances**

`tryUnlockNlpBalance` in `SpotEngineState.sol` advances the `unlockedBalanceSum` only when:

```solidity
queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
```

`getOracleTime()` in `EndpointGated.sol` calls `IEndpoint(endpoint).getTime()`, which returns `max(spotTime, perpTime)`. These timestamps are updated exclusively when the sequencer submits `SpotTick` or `PerpTick` transactions through `processTransactionImpl`. If the sequencer stops, `getOracleTime()` freezes at its last value, and no NLP balance ever satisfies the unlock condition, regardless of how much wall-clock time passes.

**Layer 2 — `BurnNlp` has no slow-mode path**

`processSlowModeTransactionImpl` in `EndpointTx.sol` handles the following transaction types permissionlessly (after the 3-day delay): `DepositCollateral`, `WithdrawCollateral`, `DepositInsurance`, `LinkSigner`, `WithdrawInsurance`, `DelistProduct`, `DumpFees`, `RebalanceXWithdraw`, `UpdateTierFeeRates`, `AddNlpPool`, `UpdateNlpPool`, `DeleteNlpPool`, `ForceRebalanceNlpPool`, `NlpProfitShare`, `UpdateBuilder`, `ClaimBuilderFee`. `BurnNlp` is absent from this list.

`BurnNlp` is only handled inside `processTransactionImpl`, which is reached exclusively via `submitTransactionsChecked` (requires `msg.sender == sequencer`) or `submitTransactionsCheckedWithGasLimit`. There is no permissionless path to execute a `BurnNlp`.

**Layer 3 — `burnNlp` itself requires unlocked balance**

Even if a user attempted to use slow-mode `WithdrawCollateral` for `NLP_PRODUCT_ID`, `Clearinghouse.burnNlp` enforces:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
```

`getNlpUnlockedBalance` calls `tryUnlockNlpBalance`, which is frozen

### Title
NLP Holder Funds Permanently Locked When Sequencer Fails — No Slow-Mode Escape for `BurnNlp` - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

When a user mints NLP tokens, their underlying quote collateral is transferred into the NLP pool and their NLP balance is time-locked for 4 days. The only on-chain path to recover that collateral is `BurnNlp`. However, `BurnNlp` is exclusively handled in the sequencer-gated `processTransactionImpl` path and is entirely absent from `processSlowModeTransactionImpl` — the permissionless escape hatch. If the sequencer fails permanently, NLP holders have no on-chain mechanism to burn their tokens and recover their collateral. The funds are permanently locked.

---

### Finding Description

**Step 1 — NLP minting locks quote collateral.**

When a user calls `MintNlp`, `Clearinghouse.mintNlp` debits the user's quote balance and credits NLP tokens:

```solidity
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
``` [1](#0-0) 

The user's quote is now inside the NLP pool. Their subaccount holds NLP tokens, not quote.

**Step 2 — NLP balance is time-locked for 4 days.**

`handleNlpLockedBalance` enqueues the minted NLP amount with an `unlockedAt` timestamp of `getOracleTime() + NLP_LOCK_PERIOD` (4 days):

```solidity
unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
``` [2](#0-1) 

`NLP_LOCK_PERIOD` is hardcoded to 4 days: [3](#0-2) 

**Step 3 — Unlock depends entirely on sequencer-driven oracle time.**

`tryUnlockNlpBalance` only releases locked NLP when `unlockedAt <= getOracleTime()`:

```solidity
queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
``` [4](#0-3) 

`getOracleTime()` calls `IEndpoint(endpoint).getTime()`, which returns `max(spotTime, perpTime)`: [5](#0-4) 

`spotTime` and `perpTime` are **only updated** when the sequencer submits `SpotTick` or `PerpTick` transactions through `processTransactionImpl`: [6](#0-5) 

If the sequencer stops, `getOracleTime()` freezes. The NLP balance never unlocks.

**Step 4 — `BurnNlp` is absent from the slow-mode escape path.**

`processSlowModeTransactionImpl` is the permissionless escape hatch (executable by anyone after 3 days). It handles `WithdrawCollateral`, `DepositCollateral`, `LinkSigner`, and several admin types — but `BurnNlp` is not among them: [7](#0-6) 

`BurnNlp` is only handled inside `processTransactionImpl`, which is exclusively reachable via the sequencer-gated `submitTransactionsChecked`: [8](#0-7) 

**Step 5 — `burnNlp` enforces the unlock check, blocking any workaround.**

Even if a user could somehow reach `Clearinghouse.burnNlp`, it explicitly requires the unlocked balance to be sufficient:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [9](#0-8) 

Since `getNlpUnlockedBalance` calls `tryUnlockNlpBalance` which depends on the frozen `getOracleTime()`, the burn would revert even if the path were accessible.

**Step 6 — Slow-mode `WithdrawCollateral` cannot substitute for `BurnNlp`.**

A user's quote balance is zero after minting NLP (it was transferred to the pool). Submitting a slow-mode `WithdrawCollateral` for quote would withdraw nothing or revert on health checks. The only way to recover quote is to burn NLP first — which requires the sequencer.

---

### Impact Explanation

NLP holders permanently lose access to their underlying quote collateral if the sequencer fails permanently. The exact corrupted state is: `nlpLockedBalanceQueues[subaccount]` entries with `unlockedAt` timestamps that can never be passed because `getOracleTime()` is frozen, and no permissionless on-chain path exists to call `burnNlp`. All quote collateral deposited by affected users remains trapped in the NLP pool with no recovery mechanism.

---

### Likelihood Explanation

The sequencer is a single centralized off-chain component. Infrastructure failure, regulatory action, or key loss would halt sequencer submissions. This is directly analogous to a permanent RNG provider failure in the reference report. Any NLP holder at the time of sequencer failure is affected — this is not a low-probability edge case for a protocol that explicitly provides a slow-mode escape hatch for other operations.

---

### Recommendation

Add `BurnNlp` as a supported transaction type in `processSlowModeTransactionImpl` in `EndpointTx.sol`. The slow-mode burn should bypass the `getOracleTime()` unlock check (or use `block.timestamp` instead of oracle time for the unlock condition) so that users can recover their collateral independently of sequencer liveness. This mirrors the recommendation in the reference report: provide an exit function that ends the locked phase without requiring the failed external dependency.

---

### Proof of Concept

1. User calls `Endpoint.depositCollateral` and then submits a sequencer-processed `MintNlp` transaction. Their quote balance is debited; they receive NLP tokens locked until `getOracleTime() + 4 days`.
2. Sequencer goes offline permanently. `times.spotTime` and `times.perpTime` in `EndpointStorage` freeze at their last values. `getOracleTime()` returns a stale timestamp.
3. User waits 3 days and calls `Endpoint.executeSlowModeTransaction` with a `WithdrawCollateral` for their quote — this withdraws nothing because their quote balance is 0 (it is in the NLP pool).
4. User attempts to construct a slow-mode `BurnNlp` transaction and submit it via `submitSlowModeTransaction`. `submitSlowModeTransactionImpl` hits the final `else { revert(); }` branch because `BurnNlp` is not a recognized slow-mode type. [10](#0-9) 
5. No other on-chain path exists. The user's collateral is permanently locked in the NLP pool.

### Citations

**File:** core/contracts/Clearinghouse.sol (L473-477)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/SpotEngine.sol (L162-165)
```text
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```

**File:** core/contracts/SpotEngineState.sol (L292-294)
```text
        while (
            queue.unlockedUpTo < queue.balanceCount &&
            queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
```

**File:** core/contracts/EndpointGated.sol (L21-23)
```text
    function getOracleTime() internal view returns (uint128) {
        return IEndpoint(endpoint).getTime();
    }
```

**File:** core/contracts/EndpointTx.sol (L202-330)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            clearinghouse.depositInsurance(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
        } else if (txType == IEndpoint.TransactionType.WithdrawInsurance) {
            clearinghouse.withdrawInsurance(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
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
        } else if (txType == IEndpoint.TransactionType.RebalanceXWithdraw) {
            clearinghouse.rebalanceXWithdraw(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.UpdateTierFeeRates) {
            IEndpoint.UpdateTierFeeRates memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateTierFeeRates)
            );
            IOffchainExchange(offchainExchange).updateTierFeeRates(txn);
        } else if (txType == IEndpoint.TransactionType.AddNlpPool) {
            IEndpoint.AddNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.AddNlpPool)
            );
            addNlpPool(txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.UpdateNlpPool) {
            IEndpoint.UpdateNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateNlpPool)
            );
            updateNlpPool(txn.poolId, txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.DeleteNlpPool) {
            IEndpoint.DeleteNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DeleteNlpPool)
            );
            deleteNlpPool(txn.poolId);
        } else if (txType == IEndpoint.TransactionType.ForceRebalanceNlpPool) {
            IEndpoint.ForceRebalanceNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ForceRebalanceNlpPool)
            );
            clearinghouse.forceRebalanceNlpPool(
                nlpPools,
                txn.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
            IEndpoint.NlpProfitShare memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.NlpProfitShare)
            );
            require(
                txn.poolId > 0 && txn.poolId < nlpPools.length,
                ERR_INVALID_NLP_POOL
            );
            require(
                nlpPools[txn.poolId].owner != address(0),
                ERR_INVALID_NLP_POOL
            );
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
                nlpPools[txn.poolId].subaccount,
                txn.recipient,
                txn.amount
            );
        } else if (txType == IEndpoint.TransactionType.UpdateBuilder) {
            IOffchainExchange(offchainExchange).updateBuilder(transaction);
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
            revert();
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L466-485)
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
        } else if (txType == IEndpoint.TransactionType.PerpTick) {
            IEndpoint.PerpTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.PerpTick)
            );
            Times memory t = times;
            uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
            perpEngine.updateStates(dt, txn.avgPriceDiffs);
            t.perpTime = txn.time;
            times = t;
```

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
