### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` to the pool. If the `SwapAllowlistExtension` keys its allowlist check on `sender` (the immediate pool caller), any disallowed user can bypass the curated-pool gate by routing through the router — exactly the same class of missing-validity-check as the `_vote()` gauge bug.

---

### Finding Description

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` value and dispatches it to every extension in `BEFORE_SWAP_ORDER`:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, ...)
)
``` [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` hook receives this `sender` and performs an `allowedSwapper[pool][sender]` lookup. When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap()`, so `sender` = router address, not the originating user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

The research document in the repository explicitly identifies this as the target bypass path:

> *"allowAll/allowedSwapper lookup keyed by pool and sender … the hook must gate the same actor the pool designers thought they were allowlisting … assert the hook cannot be bypassed by routing through an intermediate public contract."* [3](#0-2) 

---

### Impact Explanation

A pool operator deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of counterparties. Any disallowed user can bypass this gate by calling `MetricOmmSimpleRouter` instead of calling `pool.swap()` directly. If the router is itself allowlisted (a natural operational choice so that normal users can trade), the allowlist provides zero protection: every user on the network can swap freely. The pool's LP funds are exposed to any trader the operator intended to exclude, including adversarial actors who could drain liquidity at unfavorable oracle prices.

**Severity: High** — direct bypass of a fund-protecting access control on a production pool, reachable by any unprivileged user with no special setup.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps.
- Pool operators who deploy `SwapAllowlistExtension` must allowlist the router for normal users to trade at all, making the bypass trivially reachable.
- No privileged access, no special token, no malicious setup required — any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension.beforeSwap` hook should gate on the **originating user**, not the immediate pool caller. Two options:

1. **Check `recipient` instead of `sender`** if the pool's intent is to gate who receives tokens (works for direct swaps where recipient = user).
2. **Pass the original user through `extensionData`** and have the router encode the real `msg.sender` into the payload, then verify it in the extension. The extension must validate that the payload signer matches the pool's trusted router.

The cleaner long-term fix is for the pool to expose an `originSender` field that the router populates and the extension verifies, analogous to how Uniswap v4 hooks receive `hookData` with the original caller.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E.
   - allowAll[P] = false
   - allowedSwapper[P][router] = true   (router allowlisted so normal users can trade)
   - allowedSwapper[P][alice] = false   (alice is explicitly excluded)

2. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool=P, ...).

3. Router calls P.swap(recipient=alice, ...) with msg.sender = router.

4. _beforeSwap passes sender = router to E.beforeSwap.

5. E checks allowedSwapper[P][router] → true → hook returns success selector.

6. Swap executes. Alice receives tokens from the curated pool she was meant to be excluded from.
```

The `_callExtensionsInOrder` dispatcher has no mechanism to inject the original user — it only forwards what `_beforeSwap` encodes, which is always `msg.sender` of the pool call. [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
