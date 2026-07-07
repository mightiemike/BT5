### Title
Linked Signer Can Self-Escalate to Permanently Hijack a Subaccount via `LinkSigner` with `allowLinkedSigner = true` — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the fast-mode `LinkSigner` transaction is validated with `allowLinkedSigner = true`. This means the **currently registered linked signer** (a session key) is accepted as a valid signer for a `LinkSigner` transaction — the very transaction that overwrites the linked signer mapping. A malicious or compromised session key can therefore sign a new `LinkSigner` transaction pointing to an attacker-controlled address, permanently seizing full control of the victim's subaccount without any action from the subaccount owner.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch decodes the transaction and calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` resolves the permitted signer set to `{subaccount_owner, linkedSigners[sender]}`: [2](#0-1) 

`Verifier.validateSignature` then accepts the signature if it recovers to **either** the owner address or the linked signer: [3](#0-2) 

After the signature check passes, the linked signer mapping is unconditionally overwritten: [4](#0-3) 

The `linkedSigners` mapping is stored in `EndpointStorage`: [5](#0-4) 

**Broken invariant**: A linked signer is a session key — a convenience delegation. It must not be able to modify the delegation that grants it authority. Only the subaccount owner (the address embedded in the first 20 bytes of the `bytes32` subaccount identifier) should be permitted to call `LinkSigner`. The slow-mode path correctly enforces this via `validateSender`, which checks `msg.sender` against the owner address: [6](#0-5) 

The fast-mode path has no equivalent restriction, creating an asymmetry that enables the escalation.

---

### Impact Explanation

Once an attacker controls the `linkedSigners` slot for a victim subaccount, they can sign **every** other privileged fast-mode transaction that also passes `allowLinkedSigner = true`: `WithdrawCollateral` (line 418–424), `WithdrawCollateralV2` (line 442–448), `TransferQuote` (line 599–605), `LiquidateSubaccount` (line 397–403), `MintNlp` (line 539–545), and `BurnNlp` (line 559–565). [7](#0-6) 

The attacker can drain all collateral from the victim's subaccount via `WithdrawCollateral` or `TransferQuote`, or manipulate perpetual positions via `LiquidateSubaccount`. The takeover is permanent until the victim notices and submits a slow-mode `LinkSigner` to reclaim control — a 3-day delay enforced by `SLOW_MODE_TX_DELAY`. [8](#0-7) 

**Impact: 4** — Direct, complete financial loss of all assets in the victim subaccount.

---

### Likelihood Explanation

The attacker must first obtain a linked signer position on the victim's subaccount. This mirrors the external report exactly: the attacker impersonates a trusted party (e.g., a trading UI, a bot operator, or a referral program) and socially engineers the victim into signing a `LinkSigner` transaction with the attacker's address — analogous to the victim sending an authentication token over Telegram. Alternatively, a compromised or leaked session key (e.g., a hot wallet used for automated trading) is sufficient. Once the initial foothold exists, the escalation to full account control requires only a single additional signed transaction and is trivially automatable.

**Likelihood: 3** — Requires an initial social engineering step or session key compromise, but the escalation path thereafter is zero-friction and irreversible within the slow-mode delay window.

---

### Recommendation

`LinkSigner` must **not** accept the existing linked signer as a valid authorizer. The `allowLinkedSigner` flag passed to `validateSignedTx` for the `LinkSigner` branch must be changed from `true` to `false`:

```solidity
// EndpointTx.sol — processTransactionImpl, LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
-   true   // allowLinkedSigner
+   false  // only the subaccount owner may change the linked signer
);
```

This aligns the fast-mode path with the slow-mode path, which already enforces owner-only authorization via `validateSender`. [9](#0-8) 

---

### Proof of Concept

1. **Setup**: Victim `Alice` owns subaccount `alice_sub` (first 20 bytes = `alice_addr`). She legitimately links a session key `session_key` for automated trading: `linkedSigners[alice_sub] = session_key_addr`.

2. **Attacker obtains session key**: Attacker either (a) socially engineers Alice into linking `attacker_addr` directly, or (b) compromises `session_key_addr` (e.g., leaked hot wallet key).

3. **Escalation**: Using `session_key_addr`, the attacker constructs and signs a `LinkSigner` transaction:
   ```
   sender  = alice_sub
   signer  = attacker_permanent_addr  (bytes32-padded)
   nonce   = nonces[alice_addr]
   ```
   The attacker submits this to the sequencer (fast mode).

4. **Validation passes**: `validateSignedTx` calls `validateSignature(alice_sub, session_key_addr, digest, sig)`. `Verifier.validateSignature` recovers `session_key_addr` from the signature, which equals `linkedSigner` → check passes.

5. **State mutation**: `linkedSigners[alice_sub]` is overwritten with `attacker_permanent_addr`.

6. **Full takeover**: Attacker signs `WithdrawCollateral` for `alice_sub` using `attacker_permanent_addr`. `validateSignature` accepts it as the new linked signer. All of Alice's collateral is withdrawn to the attacker's wallet.

7. **Alice's only recourse** is slow-mode `LinkSigner`, which takes 3 days — by which time all assets are gone.

### Citations

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
```

**File:** core/contracts/EndpointTx.sol (L413-424)
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

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
