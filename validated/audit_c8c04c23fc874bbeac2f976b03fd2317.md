### Title
Linked Signer Can Unilaterally Replace Itself via `LinkSigner`, Enabling Full Subaccount Takeover — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner = true`. This means the currently registered linked signer for a subaccount can sign a `LinkSigner` transaction to overwrite itself with any attacker-controlled address. Because the linked signer already has authority to sign `WithdrawCollateral` transactions, a single compromised linked signer key is sufficient to permanently take over the subaccount and drain all collateral.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with the fifth argument hardcoded to `true`: [1](#0-0) 

`validateSignedTx(..., true)` routes through `validateSignature`, which passes the current linked signer as an accepted signer to `Verifier.validateSignature`: [2](#0-1) 

`Verifier.validateSignature` accepts a signature from either the subaccount owner address or the `linkedSigner` address: [3](#0-2) 

This means the linked signer can produce a valid `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]`: [4](#0-3) 

By contrast, the slow-mode `LinkSigner` path correctly restricts this operation to the subaccount owner EOA via `validateSender`: [5](#0-4) 

The asymmetry is the root cause: the slow-mode path enforces owner-only control over linked signer assignment, but the fast-mode path does not.

---

### Impact Explanation

Once the attacker replaces the linked signer with an attacker-controlled address, they can sign `WithdrawCollateral` transactions (also validated with `allowLinkedSigner = true`) to drain the subaccount's full collateral balance: [6](#0-5) 

The corrupted state is `linkedSigners[victim_subaccount]`, and the resulting asset delta is the complete loss of all collateral held in the subaccount. The original owner cannot recover control through the fast-mode path because the attacker controls the linked signer slot; recovery requires a slow-mode `LinkSigner` transaction, which has a hardcoded 3-day delay: [7](#0-6) 

During those 3 days, the attacker can drain the account.

---

### Likelihood Explanation

Linked signers are the standard mechanism for API-key-based automated trading bots in this protocol. These keys are held on internet-connected servers and are high-value targets. A server breach, key leak, or supply-chain compromise of a trading bot exposes the linked signer key. The attacker needs only one valid signature on a `LinkSigner` transaction — a single key compromise is sufficient for a complete, irreversible subaccount takeover.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`:

```solidity
// EndpointTx.sol, processTransactionImpl, LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // was: true — linked signer must NOT be able to replace itself
);
```

This aligns the fast-mode path with the slow-mode path, which already enforces owner-only control over linked signer assignment. The linked signer should retain authority to sign trading operations but never to modify the trust relationship itself.

---

### Proof of Concept

1. Alice registers subaccount `alice_sub` and sets `linked_signer_key` as her linked signer (e.g., for an automated trading bot).
2. Attacker compromises `linked_signer_key`.
3. Attacker constructs a `SignedLinkSigner` transaction: `sender = alice_sub`, `signer = attacker_address`, `nonce = current_nonce`.
4. Attacker signs the EIP-712 digest using `linked_signer_key`. `Verifier.validateSignature` accepts it because `recovered == linkedSigner`.
5. `linkedSigners[alice_sub]` is overwritten with `attacker_address`.
6. Attacker signs a `WithdrawCollateral` transaction (`sender = alice_sub`, `amount = full_balance`) using `attacker_address`. This passes because `allowLinkedSigner = true` and `getLinkedSigner(alice_sub)` now returns `attacker_address`.
7. `clearinghouse.withdrawCollateral` transfers Alice's full collateral to the default address. Alice's subaccount is drained.
8. Alice can only recover via slow-mode `LinkSigner`, but the 3-day delay window allows the attacker to complete the drain first. [8](#0-7)

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
