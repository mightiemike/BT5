### Title
Compromised Linked Signer Can Immediately Drain Subaccount While Owner's Trustless Revocation Is Delayed 3 Days — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`EndpointTx.processTransactionImpl` processes `LinkSigner` and `WithdrawCollateral` transactions with `allowLinkedSigner=true`, meaning a compromised linked signer can immediately execute both harmful withdrawals and self-perpetuating signer replacements via the sequencer fast path. The owner's only trustless, censorship-resistant on-chain remedy — submitting a `LinkSigner` revocation via `submitSlowModeTransaction` — is unconditionally subject to a hardcoded 3-day delay (`SLOW_MODE_TX_DELAY`). This creates a 3-day attack window during which the attacker retains full signing authority over the subaccount.

---

### Finding Description

The Nado protocol supports a `linkedSigners` mapping that allows a subaccount owner to delegate signing authority to a hot wallet for trading convenience. Two execution paths exist for `LinkSigner` transactions:

**Fast path (sequencer-mediated, no delay):** In `processTransactionImpl`, the `LinkSigner` branch validates the signature with `allowLinkedSigner=true`:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true   // ← linked signer may sign this
    );
    linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
```

This means the **currently registered linked signer can sign a `LinkSigner` transaction** to replace itself with another attacker-controlled address. The sequencer processes this immediately.

`WithdrawCollateral` via the same fast path also uses `allowLinkedSigner=true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← linked signer may sign withdrawals
);
clearinghouse.withdrawCollateral(...);
```

**Slow path (trustless, on-chain, 3-day delay):** `submitSlowModeTransactionImpl` unconditionally stamps every queued transaction — including `LinkSigner` revocations — with `SLOW_MODE_TX_DELAY`:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
    sender: sender,
    tx: transaction
});
```

There is no exception for defensive `LinkSigner` operations (e.g., setting `signer` to `address(0)` to revoke). The slow mode is the **only trustless, censorship-resistant on-chain path** available to the owner; the sequencer fast path requires sequencer cooperation and is not guaranteed.

---

### Impact Explanation

A compromised linked signer key enables the following attack:

1. Attacker signs a `LinkSigner` transaction (using the compromised linked signer key) to replace the linked signer with a fresh attacker-controlled address. Submitted to the sequencer → **executed immediately**.
2. Attacker signs `WithdrawCollateral` transactions using the new linked signer to drain all collateral from the subaccount. Submitted to the sequencer → **executed immediately**.
3. The legitimate owner submits a `LinkSigner` revocation via `submitSlowModeTransaction` (the only trustless on-chain path) → **queued with a 3-day delay**.
4. During those 3 days, the attacker's linked signer retains full signing authority and can continue draining any newly deposited collateral or open positions.

The corrupted state is: `linkedSigners[subaccount]` is set to an attacker-controlled address, and the owner's on-chain revocation is locked behind a 3-day queue. The asset delta is the full collateral balance of the subaccount.

---

### Likelihood Explanation

Linked signers are a core UX feature — users are expected to set hot wallets as linked signers for high-frequency trading. Hot wallet key compromise is a realistic and well-documented threat. The attack requires only a compromised linked signer key and the ability to submit transactions to the sequencer, both of which are within reach of an unprivileged attacker. No admin access, governance capture, or sequencer compromise is required.

---

### Recommendation

`LinkSigner` transactions that **revoke** a linked signer (i.e., set `signer` to `address(0)` or to the subaccount owner's own address) should be exempt from the slow mode delay. Specifically, `submitSlowModeTransactionImpl` should detect revocation-type `LinkSigner` transactions and set `executableAt = block.timestamp` (or process them immediately), analogous to how the external report recommends allowing cancellers to call `cancel` without delay.

Additionally, `processTransactionImpl` should be reviewed to determine whether `allowLinkedSigner=true` is appropriate for `LinkSigner` transactions, since it allows the linked signer to perpetuate its own access.

---

### Proof of Concept

1. Alice sets `linkedSigners[aliceSubaccount] = hotWallet` via the sequencer fast path.
2. Attacker compromises `hotWallet` private key.
3. Attacker calls `submitTransactionsChecked` (via sequencer) with:
   - A `LinkSigner` transaction signed by `hotWallet`, setting `signer = attackerWallet2`. Validated at [1](#0-0)  with `allowLinkedSigner=true`. `linkedSigners[aliceSubaccount]` is now `attackerWallet2`.
   - A `WithdrawCollateral` transaction signed by `attackerWallet2`, draining Alice's balance. Validated at [2](#0-1)  with `allowLinkedSigner=true`.
4. Alice calls `submitSlowModeTransaction` with a `LinkSigner` revocation. `submitSlowModeTransactionImpl` stamps it with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). [3](#0-2) 
5. The constant `SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60` is defined at: [4](#0-3) 
6. Alice cannot execute the revocation for 3 days. [5](#0-4) 
7. During those 3 days, `attackerWallet2` retains full signing authority and can drain any remaining or newly deposited collateral. The `linkedSigners` mapping is stored at: [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L374-384)
```text
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

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/Endpoint.sol (L196-199)
```text
        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
