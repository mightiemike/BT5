### Title
Parent-Linked Signer Implicitly Controls All Isolated Subaccounts — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`getLinkedSigner` unconditionally resolves the linked signer for any isolated subaccount to the **parent subaccount's** linked signer entry. This means a linked signer set on a parent subaccount automatically inherits authority over every isolated subaccount derived from it — with no mechanism for the user to assign an independent linked signer to an isolated subaccount or to exclude isolated subaccounts from the parent's linked signer scope.

---

### Finding Description

`getLinkedSigner` in `EndpointTx.sol` contains the following routing logic:

```solidity
function getLinkedSigner(bytes32 subaccount) public view virtual returns (address) {
    return
        RiskHelper.isIsolatedSubaccount(subaccount)
            ? linkedSigners[
                IOffchainExchange(offchainExchange).getParentSubaccount(subaccount)
              ]
            : linkedSigners[subaccount];
}
``` [1](#0-0) 

For any isolated subaccount, the function ignores `linkedSigners[isolatedSubaccount]` entirely and always returns `linkedSigners[parentSubaccount]`. This is the shared-state analog to the reported mutex sharing bug: the "lock" (linked signer) is shared between the original (parent) and its copies (isolated subaccounts).

`validateSignature` and `validateCompactSignature` both pass the result of `getLinkedSigner(sender)` as the authorized alternate signer whenever `allowLinkedSigner = true`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`validateSignedTx` is called with `allowLinkedSigner = true` for the majority of sequencer-submitted transaction types, including `LinkSigner`, `MintNlp`, `BurnNlp`, `TransferQuote`, and `CreateIsolatedSubaccount`. [3](#0-2) 

The `LinkSigner` handler stores the signer keyed by `signedTx.tx.sender`:

```solidity
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
``` [4](#0-3) 

If `signedTx.tx.sender` is an isolated subaccount, the stored value at `linkedSigners[isolatedSubaccount]` is **never read** by `getLinkedSigner`, making it impossible for a user to assign an independent linked signer to an isolated subaccount or to revoke the parent's linked signer's authority over isolated subaccounts independently.

Isolated subaccounts are identified by the `'iso'` magic suffix in their encoding:

```solidity
function isIsolatedSubaccount(bytes32 subaccount) internal pure returns (bool) {
    return uint256(subaccount) & 0xFFFFFF == 6910831;
}
``` [5](#0-4) 

The `linkedSigners` mapping is a flat `bytes32 => address` map in `EndpointStorage`, with no per-subaccount isolation between parent and derived isolated subaccounts: [6](#0-5) 

---

### Impact Explanation

A linked signer set on a parent subaccount gains full signing authority over every isolated subaccount derived from that parent. Any transaction type that passes `allowLinkedSigner = true` — including collateral withdrawals and quote transfers — can be signed by the parent's linked signer on behalf of any isolated subaccount. If the linked signer is a partially-trusted third party (e.g., a trading bot granted access to the parent for spot trading), it can unilaterally drain all isolated subaccounts without the user's knowledge or consent. The user has no mechanism to scope the linked signer to only the parent, nor to assign a different linked signer to individual isolated subaccounts.

---

### Likelihood Explanation

Medium. The scenario requires a user to have both a linked signer set on the parent subaccount and one or more isolated subaccounts with funds. This is a normal operational pattern for active traders using the protocol's isolated margin feature alongside automated trading. The implicit authority propagation is not surfaced to the user at any point in the transaction flow.

---

### Recommendation

Modify `getLinkedSigner` to first check whether the isolated subaccount has its own entry in `linkedSigners`, and only fall back to the parent's linked signer if none is set:

```solidity
function getLinkedSigner(bytes32 subaccount) public view virtual returns (address) {
    if (RiskHelper.isIsolatedSubaccount(subaccount)) {
        address isolatedSigner = linkedSigners[subaccount];
        if (isolatedSigner != address(0)) {
            return isolatedSigner;
        }
        return linkedSigners[
            IOffchainExchange(offchainExchange).getParentSubaccount(subaccount)
        ];
    }
    return linkedSigners[subaccount];
}
```

This mirrors the fix described in the reference report: give each derived entity its own independent lock rather than inheriting the parent's.

---

### Proof of Concept

1. User deploys parent subaccount `P` and sets `linkedSigners[P] = BOT`.
2. User creates isolated subaccount `I` (product-specific, derived from `P`).
3. User deposits collateral into `I`.
4. `BOT` constructs a `WithdrawCollateral` transaction with `sender = I`.
5. The sequencer calls `validateSignedTx(I, nonce, tx, BOT_sig, true)`.
6. `validateSignature` calls `getLinkedSigner(I)`, which returns `linkedSigners[P]` = `BOT`.
7. Signature verification passes; collateral is withdrawn from `I` to `BOT`-controlled address.
8. User's isolated position is drained without their explicit authorization for that isolated subaccount.

### Citations

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
```

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

**File:** core/contracts/EndpointTx.sol (L588-590)
```text
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/libraries/RiskHelper.sol (L83-89)
```text
    function isIsolatedSubaccount(bytes32 subaccount)
        internal
        pure
        returns (bool)
    {
        return uint256(subaccount) & 0xFFFFFF == 6910831;
    }
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
