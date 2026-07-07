### Title
Linked Signer Can Replace Itself, Enabling Permanent Subaccount Takeover — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-mode transaction is validated with `allowLinkedSigner = true`, meaning the currently active linked signer can sign a `LinkSigner` transaction to replace itself with any attacker-controlled address. A compromised session key can therefore permanently seize control of a subaccount and drain its collateral.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with the fifth argument hardcoded to `true`: [1](#0-0) 

`validateSignedTx(..., true)` routes through `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted alternate signer to the verifier: [2](#0-1) 

`getLinkedSigner` returns the address stored in `linkedSigners[subaccount]` (or the parent's entry for isolated subaccounts): [3](#0-2) 

Because the verifier accepts a signature from either the subaccount owner **or** the linked signer, the linked signer can craft and submit a valid `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address: [4](#0-3) 

By contrast, the slow-mode path for `LinkSigner` uses `validateSender`, which enforces that `msg.sender` equals the address embedded in the subaccount `bytes32` — i.e., only the primary wallet owner: [5](#0-4) 

The fast-mode path has no equivalent restriction, creating an asymmetry: the slow-mode path correctly limits `LinkSigner` to the primary owner, but the fast-mode path (the normal production path) does not.

---

### Impact Explanation

Once the linked signer is replaced with an attacker-controlled address, the attacker can sign any `allowLinkedSigner = true` transaction on behalf of the victim subaccount. This includes `WithdrawCollateral` and `WithdrawCollateralV2`, both of which accept linked-signer signatures: [6](#0-5) 

The attacker can drain the entire collateral balance of the subaccount. The original owner cannot recover via the fast path because the linked signer has already been replaced; recovery requires a slow-mode `LinkSigner` transaction with a 3-day delay, during which the attacker can complete the withdrawal.

---

### Likelihood Explanation

Linked signers are session keys intended for automated trading bots and front-end dApps. They are routinely stored in server environments or browser local storage. A single server compromise, leaked `.env` file, or XSS attack is sufficient to expose the session key. Once exposed, the attacker needs only one sequencer-submitted transaction to permanently take over the subaccount — no on-chain funds are required and no admin cooperation is needed.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the primary subaccount owner (the address embedded in the `sender` bytes32) should be permitted to change the linked signer. The slow-mode path already enforces this correctly via `validateSender`; the fast-mode path must match it.

```solidity
// EndpointTx.sol — processTransactionImpl, LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← was true; linked signer must not be able to rotate itself
);
```

---

### Proof of Concept

1. Alice owns subaccount `aliceSub` and sets `linkedSigners[aliceSub] = sessionKey` via a legitimate `LinkSigner` transaction.
2. Attacker obtains `sessionKey` (e.g., from a compromised server).
3. Attacker constructs a `SignedLinkSigner` with `tx.sender = aliceSub`, `tx.signer = attackerAddress`, and signs it with `sessionKey`.
4. Attacker submits the transaction to the sequencer (or via slow mode after 3 days if the sequencer is bypassed).
5. `processTransactionImpl` calls `validateSignedTx(..., true)`, which accepts the `sessionKey` signature and writes `linkedSigners[aliceSub] = attackerAddress`.
6. Attacker now signs a `WithdrawCollateralV2` with `attackerAddress`, directing Alice's collateral to an arbitrary `sendTo` address.
7. Alice's subaccount is drained; the original `sessionKey` can no longer help because it is no longer the linked signer.

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

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
