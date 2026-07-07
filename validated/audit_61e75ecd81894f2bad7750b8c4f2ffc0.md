### Title
`Endpoint.executeSlowModeTransaction()` Lacks Reentrancy Guard, Enabling `nSubmissions` Double-Increment and Fast-Withdrawal Deduplication Bypass — (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.executeSlowModeTransaction()` is a permissionless external function that increments `nSubmissions` **after** an external call chain that ultimately transfers tokens to a user-controlled address. A reentrant call during that transfer double-increments `nSubmissions`, causing the slow-mode path to process a subsequent withdrawal with a different `idx` than the one the fast-withdrawal provider marked, bypassing the `BaseWithdrawPool` deduplication guard and enabling double payment.

---

### Finding Description

`executeSlowModeTransaction()` follows a vulnerable pattern:

```solidity
// Endpoint.sol
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;   // (1) read storage into memory
    _executeSlowModeTransaction(_slowModeConfig, false);       // (2) external call chain
    nSubmissions += 1;                                         // (3) increment AFTER external call
    slowModeConfig = _slowModeConfig;                          // (4) write-back AFTER external call
}
```

Inside `_executeSlowModeTransaction`, the slot is deleted from storage and then an external call is made:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];   // slot deleted, but txUpTo only updated in memory
...
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch { ... }
```

`processSlowModeTransaction` → `processSlowModeTransactionImpl` → for a `WithdrawCollateral` slow-mode tx → `clearinghouse.withdrawCollateral(..., nSubmissions)` → `Clearinghouse.handleWithdrawTransfer` → `token.safeTransfer(withdrawPool, amount)` + `BaseWithdrawPool.submitWithdrawal(token, sendTo, amount, idx)` → `handleWithdrawTransfer(token, sendTo, amount)` → **`token.safeTransfer(sendTo, amount)`**.

If `sendTo` is a contract with a token-receive hook (e.g., ERC-777 `tokensReceived`), it executes during this final transfer. At that moment:
- `slowModeConfig.txUpTo` in **storage** is still `N` (not yet written back)
- `slowModeTxs[N]` has been **deleted**
- `nSubmissions` is still `N` (not yet incremented)

The attacker's hook calls `executeSlowModeTransaction()` again (reentrant call):
1. Reads `slowModeConfig` from storage → `txUpTo = N`
2. Reads `slowModeTxs[N]` → empty (deleted)
3. Calls `this.processSlowModeTransaction(address(0), "")` → reverts on empty bytes, caught by `try/catch`
4. **`nSubmissions += 1`** → `nSubmissions = N+1`
5. Writes `slowModeConfig.txUpTo = N+1`

Outer call resumes:
6. **`nSubmissions += 1`** → `nSubmissions = N+2`
7. Writes `slowModeConfig.txUpTo = N+1` (same value, no harm)

Net result: one real transaction processed, but `nSubmissions` advanced by **2** instead of 1.

---

### Impact Explanation

`nSubmissions` is passed as `idx` to `clearinghouse.withdrawCollateral`, which forwards it to `BaseWithdrawPool.submitWithdrawal`:

```solidity
// BaseWithdrawPool.sol
function submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) public {
    require(msg.sender == clearinghouse);
    if (markedIdxs[idx]) { return; }   // deduplication guard
    markedIdxs[idx] = true;
    minIdx = idx;
    handleWithdrawTransfer(token, sendTo, amount);
}
```

A fast-withdrawal provider submits `submitFastWithdrawal(idx, ...)` with the **expected** `nSubmissions` value at processing time. If the attacker double-increments `nSubmissions` while their own withdrawal (position N) is being processed, the **next** withdrawal (position N+1) is processed with `idx = N+2` instead of `N+1`. The fast-withdrawal provider marked `markedIdxs[N+1] = true`, but the slow-mode path checks `markedIdxs[N+2]`, which is `false`. The deduplication is bypassed and the user is paid a second time from the `WithdrawPool`.

---

### Likelihood Explanation

The trigger requires a token with a receive-callback mechanism (ERC-777 `tokensReceived`, or a custom hook). The attacker must also have two consecutive withdrawals in the slow-mode queue and a fast withdrawal submitted for the second one. While ERC-777 tokens are less common than plain ERC-20, the protocol does not restrict token types, and the permissionless `executeSlowModeTransaction()` entry point is callable by any address, making the attack reachable without any privileged access.

---

### Recommendation

Add OpenZeppelin's `ReentrancyGuardUpgradeable` to `Endpoint` and apply `nonReentrant` to `executeSlowModeTransaction()`:

```diff
+import "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";

 contract Endpoint is
     EIP712Upgradeable,
     OwnableUpgradeable,
+    ReentrancyGuardUpgradeable,
     EndpointStorage,
     IEndpoint
 {
     ...
-    function executeSlowModeTransaction() external {
+    function executeSlowModeTransaction() external nonReentrant {
```

Alternatively, move `nSubmissions += 1` and `slowModeConfig = _slowModeConfig` to **before** the external call, following the checks-effects-interactions pattern.

---

### Proof of Concept

**Setup:**
- Slow-mode queue: position N = `WithdrawCollateral` for attacker (ERC-777 token), position N+1 = `WithdrawCollateral` for attacker (same token).
- Fast-withdrawal provider submits `submitFastWithdrawal(N+1, ...)`, marking `markedIdxs[N+1] = true` and paying the attacker.

**Attack:**
1. Anyone calls `executeSlowModeTransaction()` to process position N.
2. Inside `BaseWithdrawPool.submitWithdrawal` → `token.safeTransfer(attacker, amount)` triggers attacker's `tokensReceived` hook.
3. Attacker's hook calls `executeSlowModeTransaction()` (reentrant).
4. Reentrant call reads `slowModeConfig.txUpTo = N` from storage, finds `slowModeTxs[N]` empty, executes empty tx (fails silently in try/catch), increments `nSubmissions` to `N+1`, writes `txUpTo = N+1`.
5. Outer call resumes, increments `nSubmissions` to `N+2`, writes `txUpTo = N+1`.
6. Anyone calls `executeSlowModeTransaction()` to process position N+1.
7. `clearinghouse.withdrawCollateral(..., nSubmissions)` is called with `nSubmissions = N+2`.
8. `BaseWithdrawPool.submitWithdrawal(..., N+2)`: `markedIdxs[N+2]` is `false` → deduplication bypassed → attacker receives a second payment for the same withdrawal.

**Result:** Attacker receives double payment — once from the fast withdrawal (at `idx = N+1`) and once from the slow-mode path (at `idx = N+2`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
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
    }
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
