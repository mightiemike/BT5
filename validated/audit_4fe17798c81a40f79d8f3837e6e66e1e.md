### Title
Hardcoded Gas Threshold in OOG Heuristic Enables Silent Slow-Mode Transaction Deletion, Permanently Locking Deposited Funds - (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint._executeSlowModeTransaction` uses a hardcoded `250000` gas threshold to distinguish out-of-gas (OOG) errors from normal reverts. An unprivileged caller can invoke the public `executeSlowModeTransaction()` with a crafted gas amount that defeats this heuristic, causing a pending slow-mode deposit transaction to be silently deleted from the queue without being processed. Because the corresponding token transfer already occurred and the fund-return path was explicitly removed, the deposited tokens are permanently locked in the contract.

---

### Finding Description

`_executeSlowModeTransaction` wraps the inner call in a try/catch and applies the following OOG heuristic in the catch block:

```solidity
if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
    assembly { invalid() }
}
// try return funds now removed
``` [1](#0-0) 

The logic is: if `gasleft()` after the failed inner call is ≤ 250000 **or** ≤ half of the gas at entry, treat it as OOG and call `invalid()` to propagate the failure. Otherwise, silently swallow the error and continue.

The hardcoded `250000` creates a blind spot. If an attacker supplies gas such that:

- `gasRemaining > 500000` (so `gasRemaining / 2 > 250000`), **and**
- the inner call OOGs but leaves `gasleft()` in the range `(250000, gasRemaining / 2)`

…then **neither** branch fires. The catch block exits normally. The slow-mode tx has already been deleted from the queue at line 194:

```solidity
delete slowModeTxs[_slowModeConfig.txUpTo++];
``` [2](#0-1) 

The transaction is gone from the queue, but `processSlowModeTransaction` never ran, so the deposit is never credited.

The comment `// try return funds now removed` confirms that the original refund path was deleted, leaving no recovery mechanism. [3](#0-2) 

---

### Impact Explanation

For a `DepositCollateral` slow-mode transaction:

1. `depositCollateralWithReferral` transfers tokens from the user into the `Endpoint` contract **and** enqueues a slow-mode tx.
2. An attacker calls `executeSlowModeTransaction()` with a crafted gas amount that triggers the blind spot.
3. The slow-mode tx is deleted but never executed; the deposit is never credited to the user's subaccount.
4. The tokens are permanently locked in the contract with no refund path. [4](#0-3) 

**Corrupted state:** `spotEngine` balance for the user's subaccount is never incremented, while the ERC-20 token balance of `Endpoint` is permanently increased by `amount`. This is a direct, irreversible asset loss for the depositor.

---

### Likelihood Explanation

`executeSlowModeTransaction()` is a public, permissionless function. [5](#0-4) 

An attacker only needs to:
1. Observe a pending slow-mode deposit in the queue (on-chain state, fully visible).
2. Estimate the gas cost of `processSlowModeTransaction` for that tx type (straightforward via `eth_estimateGas` off-chain).
3. Call `executeSlowModeTransaction()` with a gas value that places `gasleft()` after the inner OOG in the range `(250000, gasRemaining / 2)`.

No privileged access, no leaked keys, and no governance capture is required. The attack is griefing-for-profit if the attacker front-runs a large deposit.

---

### Recommendation

Replace the hardcoded `250000` threshold with a dynamic minimum gas requirement enforced **before** the inner call, e.g.:

```solidity
require(gasleft() >= MIN_GAS_FOR_SLOW_MODE_TX, "insufficient gas");
```

where `MIN_GAS_FOR_SLOW_MODE_TX` is set conservatively (or computed per tx type). This ensures the outer call reverts cleanly if gas is insufficient, rather than silently deleting the queued transaction. Alternatively, reinstate a fund-return path in the catch block so that a failed slow-mode deposit refunds the user's tokens before deleting the queue entry.

---

### Proof of Concept

1. Alice calls `depositCollateralWithReferral(subaccount, productId, 1000e6, "-1")`. Tokens are transferred; slow-mode tx is enqueued at index `N`. [4](#0-3) 

2. Attacker observes the pending tx on-chain. Off-chain, they estimate that `processSlowModeTransaction` for a `DepositCollateral` tx costs ~400,000 gas.

3. Attacker calls `executeSlowModeTransaction{gas: 700000}()`.
   - `gasRemaining` ≈ 695,000 at the `try` call site.
   - Inner call OOGs at ~400,000 gas consumed; `gasleft()` ≈ 295,000 after the catch.
   - Check 1: `295,000 <= 250,000` → **false**.
   - Check 2: `295,000 <= 695,000 / 2 = 347,500` → **false**.
   - Neither branch fires; the catch block exits silently. [6](#0-5) 

4. `slowModeTxs[N]` was deleted at line 194. `processSlowModeTransaction` never ran. Alice's deposit is never credited. Tokens are permanently locked in `Endpoint`.

### Citations

**File:** core/contracts/Endpoint.sol (L144-166)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
