### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the actual user. If the router is allowlisted (required for router-mediated swaps to work at all), every user — including those explicitly excluded — can bypass the curated pool's swap allowlist by routing through the router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol::swap
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this value as the first argument to the extension hook:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol::exactInputSingle
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

At this point `msg.sender` inside `pool.swap()` is the **router**, so `sender` delivered to the extension is the router's address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Consequence:** A pool admin who wants to allow router-mediated swaps must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller regardless of who the actual user is, making the allowlist completely ineffective for router-mediated paths. Conversely, if the router is not allowlisted, no user can ever swap through the router on that pool, breaking the primary supported swap path.

This is a direct structural analog to the external report's bug: the wrong variable (`router` / `sender`) is substituted where the correct variable (the actual user) should be used, causing the guard to apply to the wrong entity.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses is fully bypassed. Any non-allowlisted address can execute swaps on the pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool receives and pays out real tokens on each such swap, so the bypass directly enables unauthorized trading with fund-level consequences (unauthorized users extract output tokens from the pool at oracle price).

---

### Likelihood Explanation

The bypass requires no special privilege. Any user who knows the router is allowlisted (observable on-chain) can call the router's public `exactInputSingle` or `exactInput` functions. The router is a standard, publicly deployed periphery contract, so the bypass path is always available to any Ethereum address.

---

### Recommendation

The extension must check the **economic actor** — the address that initiated the swap and will receive or pay tokens — not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward the original `msg.sender` as a verified `sender` field in `extensionData`, and the extension should decode and verify it. This requires a trust model between the router and the extension.

2. **Check `sender` only for direct pool calls; for router-mediated calls, check the payer stored in transient context.** The cleanest solution is for the pool or router to expose the true originator so extensions can gate on it.

At minimum, document that `sender` in `beforeSwap` is the direct caller of `pool.swap()` (which may be a router), and that allowlisting the router opens the gate to all users.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so router-mediated swaps work
  - Pool admin does NOT allowlist Alice (address(0xA11CE)).

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: alice,
         zeroForOne: true,
         amountIn: X,
         ...
     });
  2. Router calls pool.swap(alice, true, X, ...).
  3. pool.swap sets sender = msg.sender = router.
  4. _beforeSwap(router, alice, ...) → SwapAllowlistExtension.beforeSwap(router, ...).
  5. allowedSwapper[pool][router] == true → check passes.
  6. Swap executes; Alice receives output tokens.

Result: Alice, who is explicitly not allowlisted, successfully swaps on the curated pool.
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
