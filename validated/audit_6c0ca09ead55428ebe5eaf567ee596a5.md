### Title
`requireValidTxSignatures` Bypassed When `nSigner == 0` — Signature Quorum Check Skipped on Uninitialized Verifier - (File: core/contracts/Verifier.sol)

---

### Summary

`Verifier.requireValidTxSignatures` contains the same structural flaw as the reported `onlyPolicyCenter` modifier: when the critical state variable `nSigner` equals zero (no signers registered), the quorum check `require(nSignatures == nSigner)` trivially passes with an empty signatures array, allowing any caller to bypass multi-sig validation entirely.

---

### Finding Description

In `Verifier.sol`, `requireValidTxSignatures` counts only non-empty entries in the provided `signatures[]` array and then asserts equality with `nSigner`:

```solidity
uint256 nSignatures = 0;
for (uint256 i = 0; i < signatures.length; i++) {
    if (signatures[i].length > 0) {
        nSignatures += 1;
        require(
            checkIndividualSignature(hashedMsg, signatures[i], uint8(i)),
            "invalid signature"
        );
    }
}
require(nSignatures == nSigner, "not enough signatures");
```

When `nSigner == 0` — which is the default storage value before any pubkey is assigned, or after all pubkeys have been deleted via `deletePubkey` — the final `require` evaluates to `require(0 == 0)`, which always passes. An attacker can call `requireValidTxSignatures` with an empty `signatures` array (or an array of zero-length entries) and the function returns without any cryptographic check.

`nSigner` is only incremented inside `_assignPubkey` when a non-zero point is registered, and decremented in `deletePubkey`:

```solidity
function _assignPubkey(uint256 i, uint256 x, uint256 y) internal {
    require(i < 8);
    if (isPointNone(pubkeys[i])) {
        nSigner += 1;
    }
    ...
}

function deletePubkey(uint256 index) public onlyOwner {
    if (!isPointNone(pubkeys[index])) {
        nSigner -= 1;
        delete pubkeys[index];
    }
    ...
}
```

The `initialize` function only assigns pubkeys for non-zero points in `initialSet`; if all entries are zero points, `nSigner` remains 0 after initialization.

`requireValidTxSignatures` is `public view` and is called from `BaseWithdrawPool.sol` as the signature gate for withdrawal operations. When `nSigner == 0`, that gate is open to any caller.

---

### Impact Explanation

Any caller can invoke `requireValidTxSignatures` with an empty `signatures[]` and it will succeed, bypassing the multi-sig quorum requirement. In `BaseWithdrawPool.sol`, this function guards withdrawal-related operations. A successful bypass allows unauthorized execution of those operations — analogous to arbitrary minting/burning of crTokens in the original report. The corrupted state is the withdrawal authorization: an unprivileged caller can satisfy the signature check without holding any valid signing key.

**Impact: 5 / 10**

---

### Likelihood Explanation

The vulnerable state (`nSigner == 0`) exists during the deployment window before `assignPubKey` is called, and can recur if all signers are removed via `deletePubkey`. The deployment window is a realistic attack surface. The likelihood is low but non-zero, matching the original report's assessment.

**Likelihood: 2 / 10**

---

### Recommendation

Mirror the fix pattern for `onlyPolicyCenter`: add an explicit guard that prevents `requireValidTxSignatures` from passing when `nSigner == 0`. For example:

```solidity
require(nSigner > 0, "no signers registered");
require(nSignatures == nSigner, "not enough signatures");
```

This ensures the function reverts rather than silently succeeding in the uninitialized state.

---

### Proof of Concept

1. Deploy `Verifier` without calling `assignPubKey` (or after calling `deletePubkey` for all registered keys), leaving `nSigner == 0`.
2. Call `requireValidTxSignatures(txn, idx, new bytes[](0))` — an empty signatures array.
3. The loop body never executes, `nSignatures` stays 0.
4. `require(0 == 0, "not enough signatures")` passes.
5. The function returns successfully with zero cryptographic verification performed.
6. Any downstream caller in `BaseWithdrawPool.sol` that relies on this check to authorize a withdrawal proceeds as if a valid quorum signature was provided. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/Verifier.sol (L69-83)
```text
    function _assignPubkey(
        uint256 i,
        uint256 x,
        uint256 y
    ) internal {
        require(i < 8);
        if (isPointNone(pubkeys[i])) {
            nSigner += 1;
        }
        pubkeys[i] = Point(x, y);
        for (uint256 s = (1 << i); s < 256; s = (s + 1) | (1 << i)) {
            isAggregatePubkeyLatest[s] = false;
        }
        emit AssignPubKey(i, x, y);
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

**File:** core/contracts/Verifier.sol (L274-289)
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
    }
```
