### Title
Missing Signer-Set Guard in `Verifier.requireValidTxSignatures()` Allows Signature Bypass in `BaseWithdrawPool.submitFastWithdrawal()` — (`File: core/contracts/Verifier.sol`, `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`Verifier.requireValidTxSignatures()` enforces `nSignatures == nSigner` at the end of its loop. When `nSigner == 0` (no signers configured or all signers deleted), the check trivially passes with an empty `signatures` array, bypassing all cryptographic verification. `BaseWithdrawPool.submitFastWithdrawal()` relies exclusively on this call for authorization, so any caller can drain the pool when the signer set is empty.

---

### Finding Description

`Verifier.requireValidTxSignatures()` iterates over the caller-supplied `signatures` array, counts non-empty entries into `nSignatures`, and then asserts:

```solidity
require(nSignatures == nSigner, "not enough signatures");
``` [1](#0-0) 

When `nSigner == 0`, the equality `0 == 0` is trivially true regardless of what `signatures` contains. An attacker passes an empty `signatures` array (or an array of zero-length entries), the loop body never executes, `nSignatures` stays at `0`, and the final `require` passes.

`nSigner` is `0` in two realistic conditions:

1. The `Verifier` is initialized with all-zero `Point[8]` entries — `isPointNone` returns `true` for every slot, so `_assignPubkey` is never called and `nSigner` is never incremented. [2](#0-1) 

2. The owner calls `deletePubkey()` for every registered index, decrementing `nSigner` to `0`. [3](#0-2) 

`BaseWithdrawPool.submitFastWithdrawal()` places its entire trust in this one call:

```solidity
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
``` [4](#0-3) 

There is no subsequent check that the signer set is non-empty. After `requireValidTxSignatures` returns, the function decodes the withdrawal target and amount from the attacker-supplied `transaction` bytes and transfers tokens out of the pool. [5](#0-4) 

---

### Impact Explanation

An attacker who observes `nSigner == 0` can call `submitFastWithdrawal()` with:
- Any `idx` value greater than `minIdx` that has not been marked
- A crafted `transaction` encoding a `WithdrawCollateral` or `WithdrawCollateralV2` payload pointing to an arbitrary `sendTo` address and a large `amount`
- An empty `signatures` array

The call passes `requireValidTxSignatures`, decodes the attacker-chosen `sendTo` and `amount`, and executes `handleWithdrawTransfer`, transferring ERC-20 tokens held by the pool to the attacker. The full token balance of any product in the pool is at risk.

---

### Likelihood Explanation

The condition `nSigner == 0` is reachable without any privileged attacker capability:

- A freshly deployed `Verifier` with an all-zero `initialSet` has `nSigner == 0` until the owner explicitly assigns keys. Any window between deployment and key assignment is exploitable.
- If the owner removes all signers (e.g., during a key rotation), `nSigner` transiently reaches `0`.

The analog to the external report is exact: the external report requires ISM to be unconfigured (`address(0)`); here the equivalent condition is the signer set being empty (`nSigner == 0`). Both are deployment/configuration states that the handler function does not guard against.

---

### Recommendation

Add an explicit guard at the top of `requireValidTxSignatures` (and symmetrically in `requireValidSignature`) to reject calls when no signers are registered:

```solidity
require(nSigner > 0, "no signers configured");
``` [6](#0-5) 

Alternatively, add the same guard inside `BaseWithdrawPool.submitFastWithdrawal()` before delegating to the verifier, mirroring the recommendation in the external report to check the security module is not `address(0)` before processing. [7](#0-6) 

---

### Proof of Concept

1. Deploy `Verifier` with `initialSet` = `[Point(0,0), ..., Point(0,0)]` (8 zero points). `nSigner` remains `0`.
2. Deploy `WithdrawPool` pointing to this `Verifier`. Pool holds 1,000,000 USDC for `productId = 1`.
3. Attacker calls:
   ```solidity
   withdrawPool.submitFastWithdrawal(
       minIdx + 1,                          // valid idx
       abi.encodePacked(
           uint8(TransactionType.WithdrawCollateral),
           abi.encode(SignedWithdrawCollateral({
               tx: WithdrawCollateral({
                   sender: bytes32(uint256(uint160(attacker))),
                   productId: 1,
                   amount: 1_000_000e6,
                   nonce: 0
               }),
               signature: ""
           }))
       ),
       new bytes[](0)                       // empty signatures array
   );
   ```
4. Inside `requireValidTxSignatures`: loop does not execute, `nSignatures = 0`, `require(0 == 0)` passes.
5. `resolveFastWithdrawal` decodes `sendTo = attacker`, `amount = 1_000_000e6`.
6. `handleWithdrawTransfer` transfers 1,000,000 USDC to the attacker. [1](#0-0) [8](#0-7)

### Citations

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L85-91)
```text
    function deletePubkey(uint256 index) public onlyOwner {
        if (!isPointNone(pubkeys[index])) {
            nSigner -= 1;
            delete pubkeys[index];
        }
        emit DeletePubkey(index);
    }
```

**File:** core/contracts/Verifier.sol (L261-266)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
```

**File:** core/contracts/Verifier.sol (L274-288)
```text
        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
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
