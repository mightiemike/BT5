### Title
Configured `withdrawFeeX18` Not Enforced in `WithdrawCollateralV2` — Users Can Withdraw Without Paying Fees - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `WithdrawCollateralV2` transaction path reads the configured `withdrawFeeX18` from the product config but only uses it as an upper bound. The actual fee charged is the user-supplied `signedTx.feeX18`, which can be zero. This is a direct analog to the reported bug: a stored configuration parameter (`withdrawFeeX18`) is not applied during execution; instead, a caller-controlled default (zero) is silently accepted.

---

### Finding Description

`WithdrawCollateral` (V1) correctly enforces the configured fee:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18, // configured fee applied
    signedTx.tx.productId
);
``` [1](#0-0) 

`WithdrawCollateralV2` diverges from this pattern. It reads `currentFeeX18` but only enforces it as a ceiling, then charges the user-supplied `signedTx.feeX18`:

```solidity
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(
    signedTx.tx.sender,
    signedTx.feeX18,   // user-controlled, can be 0
    signedTx.tx.productId
);
``` [2](#0-1) 

The `feeX18` field is part of the signed transaction body decoded from `transaction[1:]` and covered by `validateSignedTx`. The user signs a specific `feeX18` value; the sequencer cannot alter it without invalidating the signature. A user who signs with `feeX18 = 0` produces a fully valid on-chain transaction — the check `0 <= currentFeeX18` always passes regardless of the configured fee. [3](#0-2) 

The configured `withdrawFeeX18` is stored in the `SpotEngine` product config and is the protocol's intended fee for collateral withdrawals. V1 applies it directly; V2 ignores it as the enforced amount. [4](#0-3) 

---

### Impact Explanation

**Impact: Medium**

Any user can sign a `WithdrawCollateralV2` transaction with `feeX18 = 0`. The sequencer, acting as a neutral relay for valid user-signed transactions, is expected to process it. Refusing to do so constitutes censorship of a cryptographically valid request. The result is that the configured `withdrawFeeX18` — the protocol's intended revenue mechanism for withdrawals — is entirely bypassable. All withdrawal fee revenue for any product with a non-zero `withdrawFeeX18` can be zeroed out by users who sign with `feeX18 = 0`. The corrupted state delta is `sequencerFee[productId]`, which accumulates less than the configured amount, and the user's balance, which is not debited the expected fee. [5](#0-4) 

---

### Likelihood Explanation

**Likelihood: Low**

The sequencer controls which transactions are included in `submitTransactionsChecked`. An honest sequencer could enforce off-chain that `feeX18 == currentFeeX18`. However, the on-chain code provides no such guarantee, and a sequencer that processes all valid user-signed transactions (the expected neutral behavior) will accept `feeX18 = 0`. The likelihood is low because it depends on sequencer policy, but the on-chain invariant is broken. [6](#0-5) 

---

### Recommendation

Enforce the configured fee directly in `WithdrawCollateralV2`, mirroring the V1 behavior. Either remove the user-supplied `feeX18` field and always charge `currentFeeX18`, or add a minimum enforcement:

```diff
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
- require(signedTx.feeX18 >= 0);
- require(signedTx.feeX18 <= currentFeeX18);
- chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
+ chargeFee(signedTx.tx.sender, currentFeeX18, signedTx.tx.productId);
```

If the intent is to allow users to pre-commit to a fee that may have changed by execution time, the check should be `signedTx.feeX18 >= currentFeeX18` (user pays at least the configured fee), not `<=`.

---

### Proof of Concept

1. Admin sets `withdrawFeeX18 = 1e16` (1%) for product `productId = 2` via `addOrUpdateProduct`.
2. User constructs a `WithdrawCollateralV2` transaction with `feeX18 = 0`, signs it with their key.
3. Sequencer receives the signed transaction and includes it in a `submitTransactionsChecked` batch.
4. On-chain: `currentFeeX18 = 1e16`, `require(0 >= 0)` passes, `require(0 <= 1e16)` passes, `chargeFee(sender, 0, productId)` charges nothing.
5. User receives full withdrawal amount; `sequencerFee[productId]` is not incremented by the expected `1e16 * amount`. [2](#0-1)

### Citations

**File:** core/contracts/EndpointTx.sol (L130-141)
```text
    function chargeFee(bytes32 sender, int128 fee) internal {
        chargeFee(sender, fee, QUOTE_PRODUCT_ID);
    }

    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```
