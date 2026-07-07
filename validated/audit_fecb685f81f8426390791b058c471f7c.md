### Title
Unsafe catch-all OOG heuristic in `_executeSlowModeTransaction` permanently drops failed slow-mode transactions, locking deposited collateral — (`core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint._executeSlowModeTransaction` wraps `processSlowModeTransaction` in a bare `catch {}` block that silently swallows any non-OOG revert. Because the slow-mode queue entry is **deleted before execution**, a transaction that fails for any reason other than out-of-gas is permanently lost. For `DepositCollateral` slow-mode transactions, the user's ERC-20 tokens have already been transferred into the contract before the queue entry is created, so a silent failure leaves those tokens permanently unrecoverable.

---

### Finding Description

`_executeSlowModeTransaction` removes the queued entry unconditionally before attempting execution:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // deleted before try
``` [1](#0-0) 

It then wraps execution in a bare catch block that uses a gas-remaining heuristic to distinguish OOG from ordinary reverts:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [2](#0-1) 

The heuristic has two failure modes that mirror the M-04 class:

**Mode A — false-negative (silent drop):** Any ordinary revert that does *not* exhaust gas falls through the `if` guard and is silently swallowed. The queue entry is already gone. The comment `// try return funds now removed` confirms a prior fund-return path was deliberately removed, leaving no recovery mechanism.

**Mode B — false-positive (spurious `invalid()`):** A slow-mode transaction that legitimately consumes large amounts of gas before reverting (e.g., a complex health-check path) may leave `gasleft() <= gasRemaining / 2`, triggering `invalid()` and consuming all remaining gas in the outer frame. This reverts the entire sequencer batch submission.

For `DepositCollateral` slow-mode transactions, the token transfer into the contract happens in `depositCollateralWithReferral` **before** the queue entry is created:

```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
// ... then queue entry is created
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({ ... });
``` [3](#0-2) 

When the queued `DepositCollateral` entry is later executed, `processSlowModeTransactionImpl` calls `clearinghouse.depositCollateral`, which calls `_decimals(txn.productId)`:

```solidity
function _decimals(uint32 productId) internal virtual returns (uint8) {
    IERC20Base token = IERC20Base(_tokenAddress(productId));
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    return token.decimals();   // external call — can revert with empty data
}
``` [4](#0-3) 

`token.decimals()` is an external call. Tokens that do not implement `decimals()` (pre-ERC20 tokens, certain wrapped assets) revert with empty return data. The gas remaining after such a revert is typically well above both thresholds, so the `if` guard is not triggered, the error is silently swallowed, the queue entry is gone, and the user's tokens remain in the contract with no balance credit and no recovery path.

The same silent-drop applies to `WithdrawCollateral` slow-mode transactions processed in `processSlowModeTransactionImpl`:

```solidity
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, address(0), nSubmissions
);
``` [5](#0-4) 

`withdrawCollateral` calls `handleWithdrawTransfer` → `token.safeTransfer` (external), then `spotEngine.assertUtilization` (external), then a health check. Any empty revert from these calls is silently swallowed, the queue entry is deleted, and the user's withdrawal is permanently lost.

---

### Impact Explanation

For `DepositCollateral` slow-mode transactions: user ERC-20 tokens are transferred into the `Endpoint` contract before the queue entry is created. If the corresponding slow-mode execution reverts for any non-OOG reason, the tokens are permanently locked in the contract with no balance credit and no on-chain recovery path. This is a direct, irreversible asset loss.

For `WithdrawCollateral` slow-mode transactions: the queue entry is permanently deleted on any non-OOG revert, forcing the user to re-submit through the sequencer fast path. If the sequencer is unavailable or the user is sanctioned by the time they retry, the withdrawal window is lost.

---

### Likelihood Explanation

The trigger requires `processSlowModeTransaction` to revert with a non-OOG error while leaving sufficient gas that the heuristic does not fire. This is the normal behaviour of any `require` failure or external call revert. Concrete realistic triggers include:

- A collateral token that does not implement `decimals()` (many older or non-standard ERC-20s).
- A token whose `transfer` reverts with empty data (USDT-style tokens on certain chains).
- `spotEngine.assertUtilization` reverting due to pool state at execution time differing from submission time.
- Any `require` with no message string inside `depositCollateral` or `withdrawCollateral`.

All of these produce empty or short revert data and leave gas well above the 250 000 / half-remaining thresholds, so the `invalid()` branch is never taken and the error is silently dropped.

---

### Recommendation

1. **Do not delete the queue entry before execution.** Move `delete slowModeTxs[...]` into the success path, or restore it on failure.
2. **Restore a fund-return path** for `DepositCollateral` failures so that tokens transferred into the contract are returned to the depositor when the slow-mode execution fails.
3. **Replace the bare `catch {}` with typed catches** (`catch Error(string memory)` and `catch (bytes memory)`) and re-revert on any non-OOG failure rather than silently swallowing it, consistent with the `_reserveGas()`-style pattern recommended in the analogous Reserve finding.

---

### Proof of Concept

1. Token `T` is a valid collateral asset registered in `SpotEngine`. Its `decimals()` function reverts with no return data (e.g., it is a pre-ERC20 token or has been upgraded).
2. User calls `Endpoint.depositCollateral(subaccountName, productId, amount)`.
3. `handleDepositTransfer` succeeds — `amount` tokens are transferred from the user to `Endpoint`.
4. A `DepositCollateral` slow-mode queue entry is created at index `N`.
5. After `SLOW_MODE_TX_DELAY`, anyone calls `executeSlowModeTransaction()`.
6. `_executeSlowModeTransaction` deletes queue entry `N`, then calls `this.processSlowModeTransaction`.
7. Inside, `clearinghouse.depositCollateral` calls `_decimals(productId)` → `token.decimals()` → reverts with empty data.
8. The catch block fires. `gasleft()` is well above 250 000 and above `gasRemaining / 2`. The `invalid()` branch is skipped.
9. Execution returns normally. Queue entry `N` is gone. `nSubmissions` is incremented.
10. The user's `amount` tokens remain in `Endpoint` with no balance credit. No on-chain mechanism exists to recover them. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
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
    }
```

**File:** core/contracts/Clearinghouse.sol (L187-191)
```text
    function _decimals(uint32 productId) internal virtual returns (uint8) {
        IERC20Base token = IERC20Base(_tokenAddress(productId));
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        return token.decimals();
    }
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointTx.sol (L223-229)
```text
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```
