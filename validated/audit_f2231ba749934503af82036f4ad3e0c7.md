### Title
`requireValidTxSignatures` Trivially Bypassed When `nSigner == 0`, Enabling Unauthorized Fast Withdrawals — (`File: core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` enforces multi-signer quorum by checking `nSignatures == nSigner`. When `nSigner == 0` (no pubkeys registered), passing an empty `signatures[]` array satisfies this check unconditionally. `BaseWithdrawPool.submitFastWithdrawal` relies on this function as its sole authorization gate, meaning any caller can drain the pool by submitting a crafted withdrawal transaction with an empty signatures array whenever the Verifier holds no registered pubkeys.

---

### Finding Description

`Verifier.initialize` accepts an `initialSet` of 8 `Point` values and only calls `_assignPubkey` for non-zero points:

```solidity
function initialize(Point[8] memory initialSet) external initializer {
    __Ownable_init();
    for (uint256 i = 0; i < 8; ++i) {
        if (!isPointNone(initialSet[i])) {
            _assignPubkey(i, initialSet[i].x, initialSet[i].y);
        }
    }
}
```

There is no `require` that at least one point is non-zero. If all 8 entries are the zero point, `nSigner` remains `0`. [1](#0-0) 

`requireValidTxSignatures` then enforces quorum as:

```solidity
require(nSignatures == nSigner, "not enough signatures");
```

When `nSigner == 0` and `signatures.length == 0`, the loop body never executes, `nSignatures` stays `0`, and `0 == 0` passes. [2](#0-1) 

`BaseWithdrawPool.submitFastWithdrawal` calls this as its only authorization check before transferring tokens:

```solidity
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
```

After the check passes, `resolveFastWithdrawal` extracts `sendTo` and `amount` directly from the attacker-supplied `transaction` bytes, and `handleWithdrawTransfer` sends the tokens out. [3](#0-2) 

This is structurally identical to the reported `WhitelistToken` bug: a critical state variable (`nSigner` / `whitelist`) has no enforced minimum at initialization, and the security check trivially passes when that variable holds its default zero value.

---

### Impact Explanation

An attacker who observes `nSigner == 0` can:

1. Craft a `WithdrawCollateralV2` transaction bytes with `sendTo = attacker_address` and `amount = pool_balance`.
2. Call `submitFastWithdrawal(idx, craftedTx, [])` with an empty signatures array.
3. `requireValidTxSignatures` passes (`0 == 0`).
4. `handleWithdrawTransfer` sends the full token balance to the attacker.

All ERC-20 collateral held in the `WithdrawPool` (fast-withdrawal liquidity) is drained in a single transaction. [4](#0-3) 

---

### Likelihood Explanation

`nSigner == 0` is the default state of the Verifier before any pubkeys are assigned. The `initialize` function contains no guard requiring at least one valid pubkey. Any deployment where `initialize` is called with an all-zero `initialSet` — or where all pubkeys are subsequently deleted via `deletePubkey` — leaves the contract in this vulnerable state. Because `submitFastWithdrawal` is a public, permissionless function, the window of exposure is the entire period during which `nSigner == 0`. [5](#0-4) 

---

### Recommendation

Add a validation in `Verifier.initialize` (and optionally in `deletePubkey`) to enforce that `nSigner >= 1` at all times:

```solidity
require(nSigner >= 1, "at least one pubkey required");
```

Alternatively, mirror the `checkQuorum` logic inside `requireValidTxSignatures` so that `nSigner == 0` is treated as a quorum failure rather than a trivial pass:

```solidity
require(nSigner > 0 && nSignatures * 2 > nSigner, "quorum not met");
``` [6](#0-5) 

---

### Proof of Concept

1. Deploy `Verifier` and call `initialize` with `[Point(0,0), Point(0,0), ..., Point(0,0)]` (8 zero points). `nSigner` remains `0`.
2. Deploy `WithdrawPool` pointing to this `Verifier`. Fund it with 1,000,000 USDC.
3. Attacker constructs `craftedTx`:
   ```
   bytes1(uint8(TransactionType.WithdrawCollateralV2)) ++ abi.encode(
       SignedWithdrawCollateralV2({
           tx: WithdrawCollateralV2({
               sender: ...,
               productId: USDC_PRODUCT_ID,
               amount: 1_000_000e6,
               nonce: 0,
               sendTo: attacker,
               appendix: 0
           }),
           signature: ...,
           feeX18: 0
       })
   )
   ```
4. Attacker calls `submitFastWithdrawal(1, craftedTx, [])`.
5. `requireValidTxSignatures`: `nSignatures=0`, `nSigner=0`, `0==0` → passes.
6. `handleWithdrawTransfer` sends 1,000,000 USDC to attacker. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/Verifier.sol (L126-138)
```text
    function checkQuorum(uint8 signerBitmask) internal view returns (bool) {
        uint256 nSigned = 0;
        for (uint256 i = 0; i < 8; ++i) {
            bool signed = ((signerBitmask >> i) & 1) == 1;
            if (signed) {
                if (isPointNone(pubkeys[i])) {
                    return false;
                }
                nSigned += 1;
            }
        }
        return nSigned * 2 > nSigner;
    }
```

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

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
    }
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

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```
