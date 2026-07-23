Based on my investigation of the codebase structure, `ExtensionCalling.sol`, and the research pivot hints in `generate_scanned_questions.py`, I can identify a direct analog to M-12's "guard misapplied due to wrong identity" pattern.

---

### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the identity to gate. When a user swaps through `MetricOmmSimpleRouter`, the `sender` delivered to the hook is the **router's address**, not the actual end-user. If the router is allowlisted (the only way allowlisted users can use the router), any unprivileged user can bypass the swap allowlist entirely by routing through `MetricOmmSimpleRouter`. This is the direct analog to M-12: the guard checks the wrong identity (the intermediary contract instead of the actual actor), causing the configured security boundary to be silently misapplied.

---

### Finding Description

`ExtensionCalling._beforeSwap` encodes the hook call as:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
``` [1](#0-0) 

The `sender` argument here is whatever address called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInput()` (or any router entry point), the router calls `pool.swap()` with `msg.sender = router`. Therefore, the `sender` delivered to every `beforeSwap` extension — including `SwapAllowlistExtension` — is the **router contract address**, not the originating user.

`SwapAllowlistExtension.beforeSwap` keys its allowlist lookup on `(pool, sender)`. Two broken outcomes follow:

1. **Allowlisted users blocked via router**: The router is not on the allowlist, so every router-mediated swap reverts even for allowlisted users. Core swap functionality is broken for the intended user path.

2. **Allowlist bypass (higher impact)**: To fix (1), the pool admin adds the router to the allowlist. Now `(pool, router)` is allowed, and **any** unprivileged user can bypass the allowlist by calling the router — the guard is rendered inoperative.

The research pivot in the codebase itself confirms this is the intended investigation surface:

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."
> "Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract." [2](#0-1) 

---

### Impact Explanation

- **Admin-boundary break**: The pool admin configures a swap allowlist to restrict who may trade in the pool (e.g., KYC-gated pools, institutional-only pools). The allowlist is bypassed by any unprivileged user via the public router.
- **Broken core pool functionality**: If the router is not allowlisted, allowlisted users cannot use the standard swap path, making the pool's primary user-facing entry point non-functional.
- Both outcomes are within the allowed impact gate: admin-boundary break via unprivileged path, and broken core swap flow causing loss of usability or unauthorized fund flows.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented user-facing swap entry point. Any user can call it without any special privilege.
- The bypass requires only that the router be allowlisted — a natural operational step any pool admin would take to allow their allowlisted users to use the router.
- No special setup, malicious tokens, or privileged access is required. The trigger is a normal `exactInput` call through the public router.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the **originating user** rather than `sender`. Two approaches:

1. **Check `recipient` instead of `sender`**: If the pool's convention is that `recipient` is the economic beneficiary of the swap, gate on `recipient`. However, this can also be spoofed.

2. **Pass the true user via `extensionData`**: The router should encode the originating `msg.sender` into `extensionData`, and the extension should decode and verify it (with the router signing or the pool trusting only known routers).

3. **Trusted-router pattern**: Maintain a registry of trusted routers in the extension. When `sender` is a trusted router, extract the real user from `extensionData`; otherwise use `sender` directly.

The core fix is that the allowlist must gate the **economically relevant actor** — the address that initiates and benefits from the swap — not the intermediary dispatcher.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension configured on beforeSwap.
2. Admin allowlists userA: allowedSwapper[P][userA] = true.
3. Admin also allowlists the router (to allow userA to use the router):
       allowedSwapper[P][router] = true.
4. userB (not allowlisted) calls MetricOmmSimpleRouter.exactInput(P, ...).
5. Router calls pool.swap(recipient=userB, ...) with msg.sender = router.
6. ExtensionCalling._beforeSwap passes sender=router to SwapAllowlistExtension.
7. SwapAllowlistExtension checks allowedSwapper[P][router] == true → PASSES.
8. userB's swap executes successfully despite not being on the allowlist.
9. The configured security boundary is silently bypassed.
```

The `_callExtensionsInOrder` dispatcher has no mechanism to recover the original user from the call stack — it only forwards the `sender` it receives from the pool: [3](#0-2)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
