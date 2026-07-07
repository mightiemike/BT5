### Title
Compromised Linked Signer Can Permanently Hijack Subaccount and Drain Quote Balance via Self-Authorized `LinkSigner` Re-Delegation — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-mode transaction is processed with `allowLinkedSigner = true`, meaning the **currently registered linked signer** can authorize replacing itself with any new address. This creates an unbounded trust escalation: a compromised linked signer key — expected to have limited trading authority — can permanently seize full account control and drain quote balances via `TransferQuote`.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
// EndpointTx.sol:576-590
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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` resolves the permitted signer as:

```solidity
// EndpointTx.sol:172-184
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` then accepts the signature if it recovers to **either** the subaccount owner address **or** the linked signer:

```solidity
// Verifier.sol:291-304
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

There is no restriction preventing the linked signer from nominating a new linked signer. The linked signer can re-delegate itself to any attacker-controlled address.

By contrast, the slow-mode `LinkSigner` path enforces `validateSender`, which requires `msg.sender` to match the subaccount owner address — correctly restricting this operation to the owner only:

```solidity
// EndpointTx.sol:232-239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);   // ← owner-only in slow mode
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The fast-mode path has no equivalent owner-only guard.

---

### Impact Explanation

Once the linked signer is re-delegated to an attacker address, the attacker holds a persistent linked signer credential and can:

1. **Drain quote balance** — sign a `TransferQuote` (also `allowLinkedSigner = true`, EndpointTx.sol:599-604) to any attacker-controlled recipient subaccount:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← linked signer accepted for TransferQuote
);
clearinghouse.transferQuote(signedTx.tx);
``` [5](#0-4) 

2. **Lock out the legitimate owner** — the attacker's linked signer can keep re-signing `LinkSigner` to rotate the credential before the owner can revoke it via slow mode (which has a 3-day delay).

3. **Initiate collateral withdrawals** — `WithdrawCollateral` V1 also uses `allowLinkedSigner = true` (EndpointTx.sol:418-424), sending funds to `address(uint160(bytes20(sender)))` (the subaccount owner's wallet). While this specific path does not redirect funds to the attacker, it can be used to force-drain the account to the owner's EOA, disrupting open positions and triggering liquidation.

The corrupted state is: `linkedSigners[subaccount]` permanently overwritten to attacker's address; quote balance transferred to attacker's subaccount via `clearinghouse.transferQuote`. [6](#0-5) 

---

### Likelihood Explanation

Linked signers are by design hot-wallet keys used for automated trading bots, API integrations, or delegated trading. These keys have a materially higher compromise probability than a user's primary cold-wallet key. The attack requires only:

- Knowledge of the victim's subaccount identifier (public on-chain)
- A compromised linked signer private key

No sequencer compromise, governance capture, or admin access is required. The attacker submits a standard sequencer-routed transaction indistinguishable from a legitimate `LinkSigner` operation.

**Likelihood: Medium** — conditional on linked signer key compromise, but the expected blast radius of such a compromise is "limited trading authority." The actual blast radius is full account takeover, which users cannot anticipate or defend against without revoking the linked signer entirely.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` fast-mode branch in `processTransactionImpl`. The linked signer credential is a delegation from the account owner; only the owner should be permitted to change it:

```solidity
// EndpointTx.sol:581-586 — fix
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← only subaccount owner may re-delegate
);
```

This aligns the fast-mode path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner` via `validateSender`.

---

### Proof of Concept

**Setup:**
- Alice's subaccount: `aliceSub = keccak256(abi.encodePacked(alice, "default"))`
- Alice registers `botKey` as her linked signer via `LinkSigner`
- Attacker compromises `botKey`

**Attack:**

1. Attacker constructs `LinkSigner { sender: aliceSub, signer: bytes32(uint256(uint160(attacker))), nonce: currentNonce }`.
2. Attacker signs the EIP-712 digest with `botKey`.
3. Attacker submits the transaction to the sequencer. `processTransactionImpl` reaches the `LinkSigner` branch, calls `validateSignedTx(..., true)`, which calls `verifier.validateSignature(aliceSub, botKey, digest, sig)`. The recovered address equals `botKey` == `linkedSigner` → passes.
4. `linkedSigners[aliceSub]` is now set to `attacker`.
5. Attacker constructs `TransferQuote { sender: aliceSub, recipient: attackerSub, amount: fullBalance, nonce: currentNonce+1 }`.
6. Attacker signs with their own key. `validateSignedTx(..., true)` calls `verifier.validateSignature(aliceSub, attacker, digest, sig)`. Recovered address equals `attacker` == `linkedSigner` → passes.
7. `clearinghouse.transferQuote` moves Alice's entire quote balance to `attackerSub`.

Alice's quote balance is zero. The attacker holds a persistent linked signer credential and can repeat for any future deposits. [1](#0-0) [7](#0-6)

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

**File:** core/contracts/EndpointTx.sol (L593-614)
```text
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
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
