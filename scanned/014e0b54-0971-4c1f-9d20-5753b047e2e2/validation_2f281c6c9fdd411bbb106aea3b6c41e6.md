### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the **router's address**, not the user's. A pool admin who allowlists the router (the natural step to let their allowlisted users use the standard periphery) simultaneously grants every unprivileged user the ability to bypass the gate entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by the pool.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` — the **direct caller of `swap()`** — as `sender` to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So `msg.sender` of `pool.swap()` = **router**, and the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`.

The pool admin's intent is to gate specific user addresses. To allow their allowlisted users to also use the router (the standard periphery entry point for multi-hop swaps, slippage protection, etc.), the admin must call `setAllowedToSwap(pool, router, true)`. But this single action grants **every user** — including non-allowlisted ones — the ability to bypass the gate by routing through the router.

The pool admin faces an impossible dilemma:
- **Do not allowlist the router**: allowlisted users cannot use the router (broken functionality).
- **Allowlist the router**: all users bypass the allowlist (security bypass).

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, since all of them call `pool.swap()` as `msg.sender = router`.

---

### Impact Explanation

Any unprivileged user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist is rendered ineffective for all router-mediated swaps. Unauthorized users gain full swap access to a pool intended to be restricted (e.g., KYC/compliance pools, permissioned liquidity venues). Because swaps extract tokens from the pool at oracle prices, this constitutes unauthorized access to LP assets and breaks the core access-control invariant the extension is designed to enforce.

---

### Likelihood Explanation

Medium. Pool admins who configure a swap allowlist and also want their allowlisted users to use the standard router will naturally allowlist the router address. The documentation and interface give no indication that doing so opens the gate to all users. The mistake is easy to make and hard to detect without auditing the extension's identity-checking logic against the router's call path.

---

### Recommendation

The extension must check the **actual end-user identity**, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router passes the original `msg.sender` in `extensionData`; the extension decodes and verifies it. The pool admin allowlists user addresses, not the router.
2. **Dedicated router field**: Add an authenticated `originator` field to the swap call path so the pool can forward the true user identity to extensions, independent of `msg.sender`.

The current design where `sender = msg.sender of pool.swap()` is structurally incompatible with router-mediated allowlisting.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin allowlists `alice` (a KYC'd user):
   `setAllowedToSwap(pool, alice, true)`
3. Pool admin allowlists the router so `alice` can use it:
   `setAllowedToSwap(pool, router, true)`
4. `bob` (non-allowlisted) calls:
   `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`
5. Router calls `pool.swap(bob, zeroForOne, ...)` — `msg.sender` of `swap()` = router.
6. Pool calls `_beforeSwap(router, bob, ...)` — extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. `bob` successfully swaps in the restricted pool, bypassing the allowlist entirely.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
