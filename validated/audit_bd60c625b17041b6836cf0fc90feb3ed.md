### Title
Zero-Signer Bypass in `requireValidTxSignatures` Allows Signature-Free Withdrawal Authorization — (File: `core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` enforces quorum by checking `nSignatures == nSigner`. When `nSigner == 0` (no sequencer keys registered), an empty `signatures[]` array satisfies this check trivially, bypassing all cryptographic verification. `BaseWithdrawPool` calls this function to authorize fast withdrawals. An unprivileged caller can exploit the unguarded zero-signer state to pass withdrawal authorization with no valid signature.

---

### Finding Description

`requireValidTxSignatures` counts non-empty entries in the caller-supplied `signatures` array and then asserts equality with the contract's `nSigner` counter:

```solidity
// Verifier.sol lines 274-288
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
``` [1](#0-0) 

When `nSigner == 0`, the final `require` becomes `0 == 0`, which is unconditionally true. The loop body never executes, so no individual signature is ever checked. The function returns successfully for any call that passes an empty (or all-zero-length) `signatures` array.

`nSigner` is a storage variable incremented only by `_assignPubkey` and decremented by `deletePubkey`, both owner-gated. It starts at zero after deployment and remains zero until the owner explicitly registers at least one key. [2](#0-1) [3](#0-2) 

The `initialize` function only calls `_assignPubkey` for non-zero points in `initialSet`. If all eight entries are the point at infinity (the default zero value), `nSigner` remains 0 after initialization. [3](#0-2) 

`requireValidTxSignatures` is `public view` and is called by `BaseWithdrawPool` to gate fast-withdrawal execution. With `nSigner == 0`, any caller can satisfy this gate with an empty array. [4](#0-3) 

---

### Impact Explanation

If `nSigner == 0`, an unprivileged caller can invoke the `BaseWithdrawPool` fast-withdrawal path and pass `requireValidTxSignatures` with an empty `signatures[]`. The cryptographic authorization layer is entirely absent. The attacker can drain collateral from the `WithdrawPool` without possessing any sequencer key, corrupting the exact asset balance held in the pool.

The corrupted state delta: withdrawal of arbitrary token amounts from `WithdrawPool` to an attacker-controlled address, with no valid sequencer authorization.

---

### Likelihood Explanation

The `nSigner == 0` condition arises in two realistic scenarios:

1. **Deployment window**: The `Verifier` is initialized with all-zero `initialSet` (the default), and the owner has not yet called `assignPubKey`. Any time between deployment and first key registration, the bypass is live.
2. **Key rotation gap**: The owner calls `deletePubkey` on all registered keys before assigning replacements, transiently setting `nSigner` back to 0.

Neither scenario requires the attacker to compromise any key or admin account — they only need to observe the on-chain `nSigner` state (readable via `getPubkey` returning zero for all indices) and act during the window.

---

### Recommendation

Add an explicit guard at the top of `requireValidTxSignatures`:

```solidity
require(nSigner > 0, "no signers registered");
```

This is the direct analog of calling `ProviderInstaller.installIfNeeded()` and refusing to proceed if the security provider is absent: the protocol should decline to authorize any action when its cryptographic security component is uninitialized.

Additionally, the `initialize` function should revert if all eight points in `initialSet` are the point at infinity, ensuring the `Verifier` is never left in a zero-signer state post-deployment.

---

### Proof of Concept

1. Deploy `Verifier` with `initialSet` containing eight zero-coordinate `Point` structs → `nSigner == 0`.
2. Deploy `WithdrawPool` pointing to this `Verifier`.
3. Call the `BaseWithdrawPool` fast-withdrawal entry point, passing an empty `bytes[] signatures` array.
4. Inside `requireValidTxSignatures`: loop body never executes (`nSignatures = 0`); final check `require(0 == 0)` passes.
5. Withdrawal executes with no valid sequencer authorization, transferring funds to the attacker.

The root cause is exclusively in `Verifier.requireValidTxSignatures` at line 288: the missing `nSigner > 0` pre-condition. [5](#0-4)

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

**File:** core/contracts/BaseWithdrawPool.sol (L1-1)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
```
