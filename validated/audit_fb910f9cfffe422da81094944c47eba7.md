### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for any user), every non-allowlisted user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the first argument forwarded by the pool. The pool always supplies its own `msg.sender` as that argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

The pool's `msg.sender` is now the **router**, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → every non-allowlisted user can bypass the gate by routing through the router.

There is no configuration that simultaneously allows router usage for permitted users and blocks it for non-permitted users.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool (e.g., a KYC-gated or institution-only pool) by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` whenever the router is allowlisted. The swap executes at the live oracle price, draining pool liquidity and generating fees that the pool was designed to restrict to specific counterparties. This is a direct, fund-impacting bypass of the pool's access-control invariant.

---

### Likelihood Explanation

The router is the primary user-facing swap interface. Any pool that wants to support router-mediated swaps for its allowlisted users must add the router to the allowlist. Once the router is added, the bypass is unconditionally available to every address on-chain with no additional preconditions. The attacker needs no special role, no flash loan, and no oracle manipulation — a single `exactInputSingle` call suffices.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor**, not the immediate caller. Two complementary fixes:

1. **Extension-side**: Require the router to forward the originating user in `extensionData`, and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router.
2. **Router-side**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the end user) into `extensionData` on every swap call so allowlist extensions can recover the true actor.

Alternatively, document that `SwapAllowlistExtension` is incompatible with router usage and enforce this at the factory level (e.g., reject pool creation that configures both a swap allowlist extension and a router-compatible price provider).

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension in the beforeSwap hook order.
2. Pool admin allowlists Alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so Alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap() → pool's msg.sender = router
   → extension checks allowedSwapper[pool][router] == true → PASSES
   → Bob's swap executes on the curated pool.
5. Bob receives tokens from the restricted pool with no allowlist enforcement.
```

**Affected files and lines:**

- `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) instead of the end user. [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`. [3](#0-2) 
- `ExtensionCalling._beforeSwap` confirms `sender` is the pool's `msg.sender` forwarded verbatim. [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
