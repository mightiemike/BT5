### Title
`processSlowModeTransactionImpl` Does Not Increment Nonce for `WithdrawCollateral`, Enabling Double Withdrawal via Sequencer Replay — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

When a user executes a `WithdrawCollateral` through the slow-mode fallback path (`executeSlowModeTransaction` → `processSlowModeTransactionImpl`), the user's on-chain nonce is never incremented. Because the sequencer path (`processTransactionImpl`) validates and consumes the nonce only when it processes the transaction, a previously signed `WithdrawCollateral` submitted to the sequencer remains replayable after the slow-mode execution completes. When the sequencer resumes, it can process the same signed transaction a second time, causing a double withdrawal of collateral.

---

### Finding Description

Nado exposes two execution paths for user-initiated `WithdrawCollateral` transactions:

**Path 1 — Sequencer path** (`processTransactionImpl`, `EndpointTx.sol` lines 413–436):

```solidity
IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
    transaction[1:], (IEndpoint.SignedWithdrawCollateral)
);
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,   // ← nonce consumed here
    transaction,
    signedTx.signature,
    true
);
clearinghouse.withdrawCollateral(...);
```

`validateSignedTx` calls `validateNonce`, which executes `nonces[address]++`, consuming the nonce.

**Path 2 — Slow-mode fallback path** (`processSlowModeTransactionImpl`, `EndpointTx.sol` lines 217–229):

```solidity
IEndpoint.WithdrawCollateral memory txn = abi.decode(
    transaction[1:], (IEndpoint.WithdrawCollateral)
);
validateSender(txn.sender, sender);   // ← only checks msg.sender
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, address(0), nSubmissions
);
// ← no validateNonce call; nonces[address] is never incremented
```

The `WithdrawCollateral` struct itself carries a `nonce` field (defined in `IEndpoint.sol` lines 80–85), but `processSlowModeTransactionImpl` never reads or validates it. The developer comment at line 200–201 of `EndpointTx.sol` ("TODO: these do not need senders or nonces") confirms the nonce is intentionally ignored in slow mode, but the cross-path replay consequence is unaddressed.

The nonce state lives in `nonces[address]` (incremented at `EndpointTx.sol` line 74 via `nonces[...]++`). Because the slow-mode path never touches this mapping, the nonce remains at its pre-execution value after a slow-mode withdrawal completes.

---

### Impact Explanation

**Impact: High**

A user who executes a withdrawal via slow mode and whose original signed transaction is later processed by the sequencer will have their collateral withdrawn twice. Concretely:

- User's subaccount balance is debited twice for the same intended withdrawal amount.
- The protocol transfers real ERC-20 tokens to the user twice via `clearinghouse.withdrawCollateral` → `handleWithdrawTransfer`.
- The second execution passes the health check (`getHealth >= 0`) only if the subaccount still has sufficient collateral; if it does, the protocol suffers a direct asset loss equal to the withdrawal amount.
- If the subaccount does not have sufficient collateral for the second withdrawal, the transaction reverts — but the sequencer's queue is corrupted (the nonce is consumed on the second attempt, blocking all subsequent signed transactions from that subaccount with higher nonces).

---

### Likelihood Explanation

**Likelihood: Medium**

The slow-mode mechanism exists precisely because sequencer downtime is an anticipated operational scenario. The 3-day delay (`SLOW_MODE_TX_DELAY`) is the designed window during which a user waits for the sequencer to recover before resorting to slow mode. A user who submitted a signed withdrawal to the sequencer and then used slow mode as a fallback is the exact intended use case. When the sequencer resumes and processes its backlog, it will naturally replay any pending signed transactions — including the one already executed via slow mode.

---

### Recommendation

In `processSlowModeTransactionImpl`, call `validateNonce` for every transaction type that also appears in the sequencer path with nonce validation. For `WithdrawCollateral`:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:], (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
+   validateNonce(txn.sender, txn.nonce);   // consume nonce to prevent sequencer replay
    clearinghouse.withdrawCollateral(
        txn.sender, txn.productId, txn.amount, address(0), nSubmissions
    );
```

Apply the same fix to `LinkSigner` in `processSlowModeTransactionImpl` (line 232–239), which has the same structural omission.

---

### Proof of Concept

1. User's current nonce: `nonces[user] = 5`.
2. User signs `WithdrawCollateral{sender: user, productId: 0, amount: 1000e6, nonce: 5}` and submits it to the sequencer.
3. Sequencer goes offline. User calls `submitSlowModeTransaction` with the unsigned `WithdrawCollateral` bytes (no signature, no nonce consumed).
4. After 3 days, user calls `executeSlowModeTransaction`. This routes to `processSlowModeTransactionImpl` → `WithdrawCollateral` branch. `clearinghouse.withdrawCollateral` executes, transferring 1000 USDC to the user. `nonces[user]` remains `5`.
5. Sequencer comes back online. It processes its backlog and submits the original `SignedWithdrawCollateral{nonce: 5}` via `submitTransactionsChecked` → `processTransactionImpl`.
6. `validateNonce(user, 5)` checks `5 == nonces[user]++` → passes, increments nonce to `6`.
7. `clearinghouse.withdrawCollateral` executes again, transferring another 1000 USDC to the user.
8. Net result: user received 2000 USDC for a single intended 1000 USDC withdrawal; protocol lost 1000 USDC.

**Key code references:**

- Nonce increment (sequencer path only): [1](#0-0) 
- Sequencer path — `WithdrawCollateral` with nonce validation: [2](#0-1) 
- Slow-mode path — `WithdrawCollateral` without nonce validation: [3](#0-2) 
- Developer comment acknowledging nonces are unused in slow mode: [4](#0-3) 
- `WithdrawCollateral` struct (contains `nonce` field, never consumed in slow mode): [5](#0-4) 
- `executeSlowModeTransaction` entry point (user-callable): [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L200-201)
```text
    // TODO: these do not need senders or nonces
    // we can save some gas by creating new structs
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
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
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L80-85)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
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
