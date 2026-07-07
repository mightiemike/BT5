### Title
USDC-Blacklisted Withdrawal Recipient Silently Consumes Slow-Mode Queue Entry and Permanently Blocks Fast-Withdrawal Path — (`core/contracts/BaseWithdrawPool.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

Both the fast-withdrawal path (`BaseWithdrawPool.submitFastWithdrawal`) and the sequencer-driven slow-mode withdrawal path (`Clearinghouse.withdrawCollateral` → `BaseWithdrawPool.submitWithdrawal`) use a push-funds pattern: they call `token.safeTransfer(sendTo, amount)` directly to the recipient. If `sendTo` is USDC-blacklisted at execution time, the transfer reverts. In the fast path this permanently prevents the idx from ever being marked and wastes every provider's gas. In the slow path the revert is silently swallowed by the `try/catch` in `_executeSlowModeTransaction`, the slow-mode queue entry is consumed and deleted, but the user's on-chain balance is never decremented — leaving the user's funds inaccessible until they discover the silent failure and re-submit a V2 withdrawal to a different address.

---

### Finding Description

**Fast-withdrawal path (`BaseWithdrawPool.submitFastWithdrawal`)**

```
markedIdxs[idx] = true;          // line 88  — written before transfer
...
fees[productId] += fee;           // line 111
handleWithdrawTransfer(token, sendTo, transferAmount);  // line 113 — reverts if blacklisted
```

`handleWithdrawTransfer` calls `token.safeTransfer(to, uint256(amount))` (line 189). `ERC20Helper.safeTransfer` wraps the call and `require`s success (lines 17–20 of `ERC20Helper.sol`). If `to` is USDC-blacklisted the entire transaction reverts, rolling back `markedIdxs[idx] = true`. The idx is never consumed. Because `require(idx > minIdx)` (line 87) gates every future attempt, once `minIdx` advances past this idx through other users' successful withdrawals, the fast-withdrawal slot for this idx is permanently unprocessable.

**Slow-mode withdrawal path (`Clearinghouse.withdrawCollateral` → `BaseWithdrawPool.submitWithdrawal`)**

```solidity
// Clearinghouse.sol lines 383-384
token.safeTransfer(withdrawPool, uint256(amount));
BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
```

`submitWithdrawal` (lines 127–131) sets `markedIdxs[idx] = true` and `minIdx = idx` before calling `handleWithdrawTransfer(token, sendTo, amount)`. If `sendTo` is blacklisted, the inner `safeTransfer` reverts, rolling back all state including the transfer to `withdrawPool`. This revert propagates through `Clearinghouse.withdrawCollateral` (line 408) and into `processSlowModeTransaction`, which is wrapped in a `try/catch` in `_executeSlowModeTransaction`:

```solidity
// Endpoint.sol lines 193-227
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // consumed BEFORE try/catch

try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    // "try return funds now removed"  ← comment at line 226
}
```

The slow-mode entry is deleted at line 194 **before** the `try`. The `catch` block is empty (aside from the OOG heuristic). No event is emitted on failure. The user's `spotEngine` balance is never decremented (the inner call reverted), so the funds remain in the protocol — but the queue entry is gone. The user must independently discover the silent failure and re-submit a `WithdrawCollateralV2` pointing to a non-blacklisted `sendTo`.

---

### Impact Explanation

- **Fast path**: Any fast-withdrawal provider calling `submitFastWithdrawal` for a blacklisted `sendTo` wastes gas on every attempt. Once `minIdx` advances past the idx, the slot is permanently unprocessable via the fast path.
- **Slow path**: The slow-mode queue entry is silently consumed with no on-chain signal. The user's collateral remains locked in the protocol with no automatic recovery. If the user is also OFAC-sanctioned (checked at `submitSlowModeTransactionImpl` line 375 via `requireUnsanctioned`), they cannot even submit a new withdrawal transaction, making the funds permanently inaccessible through normal protocol flows.
- **Corrupted state**: `slowModeTxs[txUpTo]` is deleted (queue state corrupted) without the corresponding balance update, breaking the invariant that every consumed withdrawal queue entry results in a balance decrement.

---

### Likelihood Explanation

USDC maintains an on-chain blacklist. A user who deposits USDC collateral and later gets blacklisted (e.g., due to regulatory action, address compromise, or protocol-level sanctions) will trigger this path on their next withdrawal attempt. The scenario is realistic for any protocol that accepts USDC as collateral and operates under regulatory jurisdictions where USDC blacklisting occurs. Likelihood is **low** in absolute terms but non-negligible given USDC's active blacklist usage.

---

### Recommendation

Replace the push-funds pattern in `handleWithdrawTransfer` (both in `BaseWithdrawPool` and `Clearinghouse`) with a pull-payment pattern:

1. On transfer failure, record `(recipient, token, amount)` in a `pendingWithdrawals` mapping instead of reverting.
2. Expose a public `claimWithdrawal()` function allowing the recipient (or any address they designate) to pull the funds later.
3. Emit an event on failed transfer so off-chain monitoring can alert users.

This mirrors the fix recommended in the external report and eliminates the silent queue-consumption bug.

---

### Proof of Concept

**Slow-path silent consumption:**

1. Alice deposits 10,000 USDC into Nado (productId = USDC spot product).
2. Alice's address is added to the USDC blacklist (e.g., by Circle).
3. Alice submits a `WithdrawCollateral` slow-mode transaction via `submitSlowModeTransaction`.
4. Sequencer includes an `ExecuteSlowMode` transaction in `submitTransactionsChecked`.
5. `_executeSlowModeTransaction` deletes `slowModeTxs[txUpTo]` at line 194.
6. `processSlowModeTransaction` → `processSlowModeTransactionImpl` → `clearinghouse.withdrawCollateral(alice, productId, amount, address(0), idx)`.
7. `Clearinghouse.handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, amount)` — succeeds.
8. `BaseWithdrawPool.submitWithdrawal` calls `token.safeTransfer(alice, amount)` — **reverts** (USDC blacklist).
9. Revert propagates; `token.safeTransfer(withdrawPool, amount)` also reverts (same call frame).
10. `catch` block in `_executeSlowModeTransaction` swallows the revert silently.
11. Alice's `spotEngine` balance is unchanged. The slow-mode entry is gone. No event emitted.
12. Alice's 10,000 USDC is inaccessible with no on-chain indication of failure.

**Fast-path permanent block:**

1. Same setup; Alice has a signed `WithdrawCollateral` with sequencer-assigned idx = 42.
2. Fast-withdrawal provider calls `submitFastWithdrawal(42, transaction, signatures)`.
3. `markedIdxs[42] = true` is written; fee logic runs.
4. `handleWithdrawTransfer(token, alice, amount)` → `token.safeTransfer(alice, amount)` → **reverts**.
5. Entire tx reverts; `markedIdxs[42]` is rolled back.
6. Provider retries — same result every time.
7. Once `minIdx` advances to ≥ 42 via other users' withdrawals, `require(idx > minIdx)` permanently blocks any future attempt for idx 42. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/Endpoint.sol (L185-228)
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
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
