### Title
Sequencer Can Inflate `feeX18` Beyond User Consent in `WithdrawCollateralV2` — (File: `core/contracts/EndpointTx.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `feeX18` field of `SignedWithdrawCollateralV2` is **not included in the EIP-712 signed digest**. The sequencer who submits the transaction can freely set `feeX18` to any value between `0` and `currentFeeX18` without the user's knowledge or consent. This is a direct analog to the external report: just as a malicious bot can inflate `tx.gasprice` to drain a user's fee token balance, the sequencer can inflate `feeX18` to drain the user's collateral balance up to the protocol-configured maximum.

---

### Finding Description

In `EndpointTx.processTransactionImpl()`, the `WithdrawCollateralV2` branch decodes a `SignedWithdrawCollateralV2` struct and charges the user `signedTx.feeX18`:

```solidity
int128 currentFeeX18 = spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(signedTx.tx.sender, signedTx.feeX18, signedTx.tx.productId);
``` [1](#0-0) 

The `SignedWithdrawCollateralV2` struct is:

```solidity
struct SignedWithdrawCollateralV2 {
    WithdrawCollateralV2 tx;
    CompactSignature signature;
    int128 feeX18;   // <-- NOT covered by the user's signature
}
``` [2](#0-1) 

The EIP-712 digest computed in `Verifier.computeDigest()` for `WithdrawCollateralV2` covers only the fields of the inner `WithdrawCollateralV2` struct:

```
WithdrawCollateralV2(bytes32 sender, uint32 productId, uint128 amount,
                     uint64 nonce, address sendTo, uint128 appendix)
``` [3](#0-2) 

The digest computation confirms `feeX18` is absent:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    signedTx.tx.nonce,
    signedTx.tx.sendTo,
    signedTx.tx.appendix   // feeX18 is NOT here
));
``` [4](#0-3) 

The `chargeFee` function deducts the fee directly from the user's spot engine balance and credits `sequencerFee`:

```solidity
function chargeFee(bytes32 sender, int128 fee, uint32 productId) internal {
    spotEngine.updateBalance(productId, sender, -fee);
    sequencerFee[productId] += fee;
}
``` [5](#0-4) 

---

### Impact Explanation

The sequencer can submit any valid user-signed `WithdrawCollateralV2` transaction with `feeX18` set to `currentFeeX18` (the maximum). The user signed only the inner `WithdrawCollateralV2` fields; they never committed to a specific fee value. The sequencer can therefore charge the maximum configured fee on every withdrawal, draining the user's collateral balance by up to `currentFeeX18` per operation. The drained amount accumulates in `sequencerFee` and is later claimed by the protocol via `DumpFees`. The corrupted state delta is: `user.spotBalance[productId] -= currentFeeX18` per withdrawal, with no user-side bound or consent. [6](#0-5) 

---

### Likelihood Explanation

Every `WithdrawCollateralV2` transaction is submitted by the sequencer. The sequencer has full, unconstrained control over `feeX18` within `[0, currentFeeX18]` for every such transaction. No additional compromise or privilege escalation is required — the design flaw is structural. This is directly analogous to the external report's bot inflating `tx.gasprice`: the sequencer is the trusted intermediary that processes user-signed transactions, and the fee parameter is outside the scope of the user's signature.

---

### Recommendation

Include `feeX18` in the EIP-712 signed digest for `WithdrawCollateralV2`. Update the type string and digest computation in `Verifier.sol`:

```
WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,
                     uint64 nonce,address sendTo,uint128 appendix,int128 feeX18)
```

And add `signedTx.feeX18` to the `abi.encode(...)` call in `computeDigest`. This ensures the user explicitly consents to the exact fee they will be charged, preventing the sequencer from unilaterally inflating it. [3](#0-2) 

---

### Proof of Concept

1. User signs a `WithdrawCollateralV2` transaction for `productId=1`, `amount=1000e6`, `nonce=5`, `sendTo=userAddress`. The signed digest covers only these fields — `feeX18` is absent.
2. Suppose `spotEngine.getConfig(1).withdrawFeeX18 = 50e18` (50 USDC equivalent).
3. The sequencer constructs `SignedWithdrawCollateralV2 { tx: <user-signed fields>, signature: <valid>, feeX18: 50e18 }`.
4. `processTransactionImpl` validates the signature (passes, because `feeX18` is not in the digest), checks `0 <= 50e18 <= 50e18` (passes), and calls `chargeFee(user, 50e18, 1)`.
5. `spotEngine.updateBalance(1, user, -50e18)` — user's collateral is reduced by the maximum fee.
6. The sequencer could have set `feeX18 = 0` (zero cost to user) but instead chose the maximum, with no on-chain mechanism preventing this. [1](#0-0) [4](#0-3)

### Citations

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
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

**File:** core/contracts/interfaces/IEndpoint.sol (L106-110)
```text
    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```

**File:** core/contracts/Verifier.sol (L24-25)
```text
    string internal constant WITHDRAW_COLLATERAL_V2_SIGNATURE =
        "WithdrawCollateralV2(bytes32 sender,uint32 productId,uint128 amount,uint64 nonce,address sendTo,uint128 appendix)";
```

**File:** core/contracts/Verifier.sol (L357-372)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(WITHDRAW_COLLATERAL_V2_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.productId,
                    signedTx.tx.amount,
                    signedTx.tx.nonce,
                    signedTx.tx.sendTo,
                    signedTx.tx.appendix
                )
            );
```
