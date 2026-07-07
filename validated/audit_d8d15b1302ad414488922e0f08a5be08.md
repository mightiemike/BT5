### Title
`requireValidTxSignatures` Signature Check Bypassed When `nSigner = 0`, Enabling Unauthorized Fast Withdrawals — (`core/contracts/Verifier.sol`, `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` enforces the check `require(nSignatures == nSigner)`. When `nSigner` is zero — because `Verifier.initialize` places no lower-bound requirement on the number of registered pubkeys — the check resolves to `require(0 == 0)` and passes unconditionally with an empty `signatures` array. `BaseWithdrawPool.submitFastWithdrawal` is the sole caller of this function and is publicly reachable by any address. An attacker who observes a zero-signer state can drain the WithdrawPool without any valid cryptographic credential.

---

### Finding Description

**Root cause — `Verifier.sol`**

`nSigner` is declared as an internal `uint256` and is incremented only inside `_assignPubkey`, which is called from `initialize` and the owner-gated `assignPubKey`. [1](#0-0) 

`initialize` iterates over the eight supplied points and skips any that are `Point(0,0)`: [2](#0-1) 

There is no `require(nSigner > 0)` after the loop. If the caller passes an all-zero `initialSet` (the Solidity default for a `Point[8]` memory array), `nSigner` remains `0` and the contract is live with no registered signers.

`requireValidTxSignatures` counts only non-empty entries in the supplied `signatures` slice and then asserts exact equality with `nSigner`: [3](#0-2) 

When `nSigner == 0`, passing `signatures = []` (length zero) yields `nSignatures = 0`, and `require(0 == 0)` passes. No signature is ever verified.

**Contrast with `requireValidSignature` (Schnorr path)**

`checkQuorum` returns `nSigned * 2 > nSigner`. With `nSigner = 0` and `nSigned = 0`, this evaluates to `0 > 0 = false`, so the Schnorr path correctly rejects a zero-signer state. The ECDSA path (`requireValidTxSignatures`) does not have an equivalent guard, creating an asymmetric bypass. [4](#0-3) 

**Reachable entry point — `BaseWithdrawPool.submitFastWithdrawal`**

`submitFastWithdrawal` is `public` with no access restriction. It calls `requireValidTxSignatures` and, on success, immediately decodes the attacker-supplied `transaction` bytes to extract `productId`, `sendTo`, and `amount`, then transfers tokens: [5](#0-4) 

The only other guards are `!markedIdxs[idx]` and `idx > minIdx`. `minIdx` starts at `0`, so any `idx ≥ 1` satisfies the second check on a fresh pool.

---

### Impact Explanation

An attacker who calls `submitFastWithdrawal` with:
- any unused `idx > minIdx`
- a crafted `transaction` encoding `sendTo = attacker`, `productId = <any registered product>`, `amount = pool_balance`
- `signatures = []`

will pass all checks and receive `amount − fee` tokens from the WithdrawPool. Because `sendTo == msg.sender` triggers the fee-deduction branch rather than a `safeTransferFrom`, the attacker pays no upfront fee. All collateral held in the pool for any registered product can be stolen in a single transaction.

**Corrupted state delta**: `IERC20Base.balanceOf(WithdrawPool)` drops to zero; `fees[productId]` is incremented by the fee amount; `markedIdxs[idx]` is set to `true` (preventing replay of the same `idx`, but the attacker only needs one call per product).

---

### Likelihood Explanation

The precondition is `nSigner = 0`. This arises in two realistic scenarios:

1. **Deployment with default/zero pubkeys.** The `initialize` signature accepts a `Point[8]` memory array. A deployment script that passes the zero-value default (e.g., `new Verifier.Point[](8)`) silently produces a live contract with `nSigner = 0`. There is no on-chain guard preventing this.
2. **Owner deletes all signers.** `deletePubkey` is owner-callable and decrements `nSigner` without a floor check. Deleting all eight slots brings `nSigner` to `0`.

Scenario 1 is a realistic misconfiguration risk (medium likelihood). Scenario 2 requires owner action but has no code-level safeguard.

---

### Recommendation

1. Add `require(nSigner > 0, "no signers registered")` at the top of `requireValidTxSignatures`.
2. Add `require(nSigner > 0, "must register at least one signer")` at the end of `initialize`.
3. Add a floor check in `deletePubkey` to prevent `nSigner` from reaching zero: `require(nSigner > 1, "cannot remove last signer")`.

---

### Proof of Concept

```
// Precondition: Verifier initialized with Point[8] of all zeros → nSigner = 0

// Attacker constructs a WithdrawCollateral transaction:
bytes memory tx = abi.encodePacked(
    uint8(TransactionType.WithdrawCollateral),
    abi.encode(SignedWithdrawCollateral({
        tx: WithdrawCollateral({
            sender: bytes32(uint256(uint160(attacker))),  // sendTo resolves to attacker
            productId: USDC_PRODUCT_ID,
            amount: poolBalance,
            nonce: 0
        }),
        signature: ""   // ignored — check is bypassed
    }))
);

// Call with empty signatures array
withdrawPool.submitFastWithdrawal(
    1,          // idx > minIdx (0)
    tx,
    new bytes[](0)   // nSignatures = 0 == nSigner = 0 → passes
);

// Result: attacker receives poolBalance − fee USDC
``` [6](#0-5) [5](#0-4)

### Citations

**File:** core/contracts/Verifier.sol (L16-16)
```text
    uint256 internal nSigner;
```

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
