### Title
`SwapAllowlistExtension` checks the router's address as the swapper instead of the actual user, allowing any non-allowlisted user to bypass the swap gate by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives a `sender` argument that is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The allowlist check therefore gates the router address, not the economic actor. If the pool admin allowlists the router to support router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...) [msg.sender = router]
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

The pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. It never inspects the actual user who initiated the transaction.

**Bypass scenario:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC-verified addresses.
2. Pool admin allowlists the router (`allowedSwapper[pool][router] = true`) so that permitted users can swap through the standard periphery path.
3. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
4. The router calls `pool.swap()`; the pool passes `msg.sender = router` to the extension.
5. The extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. The attacker executes a swap on a pool they are explicitly barred from.

**Alternate broken-functionality scenario (no router allowlisted):**

If the pool admin does *not* allowlist the router, then even explicitly permitted users cannot swap through the router, because the extension sees `sender = router` and rejects it. The allowlist becomes incompatible with the supported periphery path entirely.

---

### Impact Explanation

A non-allowlisted user can trade on a curated pool (e.g., a KYC-gated or institutional pool) by routing through the public `MetricOmmSimpleRouter`. This is a direct, fund-impacting bypass of the pool's access-control policy: the attacker receives swap output they are not entitled to, and the pool's LP providers are exposed to counterparties the pool admin explicitly excluded. This matches the "wrong-actor binding" impact class: the guard keys authorization to the wrong actor (router instead of user), with direct loss-of-policy consequences on every curated pool that allowlists the router.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a natural and expected configuration for any curated pool that intends to support the standard periphery UX. No privileged access, no malicious setup, and no non-standard tokens are needed. Any public user can execute the bypass with a single `exactInputSingle` call.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original payer/user through the router as part of `extensionData` or a dedicated field**, and have the extension decode and verify that identity. This requires a protocol-level convention for how the router communicates the real initiator.

2. **Alternatively, check `sender` only when `sender` is not a known router**, and require the router to attest the real user in `extensionData`. The extension would decode the attested address and check it against the allowlist.

The simplest safe fix is to have `MetricOmmSimpleRouter` encode `msg.sender` (the real user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router, rather than checking `sender` directly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][router] = true   // to support router UX
  pool admin: allowedSwapper[pool][alice]  = true   // alice is KYC'd
  bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes; bob receives output tokens

Result:
  bob, who is explicitly excluded from the curated pool,
  successfully swaps by routing through the public router.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
