The full call chain is now clear. Let me trace it precisely.

**Direct swap path:**
- User → `MetricOmmPool.swap()` → `_beforeSwap(msg.sender=user, ...)` → `SwapAllowlistExtension.beforeSwap(sender=user)` → checks `allowedSwapper[pool][user]`

**Router-mediated swap path:**
- User → `MetricOmmSimpleRouter.exactInputSingle()` → `IMetricOmmPoolActions(pool).swap(...)` (msg.sender = router) → `_beforeSwap(msg.sender=router, ...)` → `SwapAllowlistExtension.beforeSwap(sender=router)` → checks `allowedSwapper[pool][router]`

The `sender` passed to `beforeSwap` is always `msg.sender` from the pool's perspective — the immediate caller, not the originating user. [1](#0-0) [2](#0-1) [3](#0-2) 

The router never encodes the originating user's identity into `extensionData` — it passes `""` or opaque callback data, not the user address. [4](#0-3) 

**The structural problem:** For a pool with `SwapAllowlistExtension` to support router-mediated swaps at all, the pool admin must allowlist the router address. But once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call through the router — the hook cannot distinguish a legitimate allowlisted user from an arbitrary attacker both calling through the same router. The allowlist is effectively nullified for all router-mediated traffic.

**Why the "trusted owner" rejection does not apply here:** The pool admin allowlisting the router is not a malicious or unusual action — it is the only way to enable router-mediated swaps for legitimate allowlisted users. The flaw is that the extension's design makes it structurally impossible to simultaneously (a) restrict swaps to specific users and (b) support the router. The design flaw is in the extension code, not in an admin making a bad choice.

**The `addLiquidity`/owner-payer separation angle in the question is a red herring.** `addLiquidity` calls `_beforeAddLiquidity`, not `_beforeSwap`. The `SwapAllowlistExtension` only implements `beforeSwap`. That part of the question does not contribute to the actual attack path.

---

### Title
Router-mediated swaps bypass `SwapAllowlistExtension` because `beforeSwap` keys authorization on the router address, not the originating user — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. Any pool that allowlists the router (required to support router-mediated swaps for legitimate users) becomes fully open to all router callers, defeating the allowlist entirely.

### Finding Description
In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` — the immediate caller — as `sender` to the extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` and calls the extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [6](#0-5) 

When the router calls `pool.swap(...)`, `sender = address(router)`. The router passes `""` as `callbackData` and user-supplied `extensionData` — neither encodes the originating user's address. [4](#0-3) 

Result: the hook checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the router is allowlisted, every user who calls through the router passes the gate regardless of their individual allowlist status.

### Impact Explanation
Any user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The pool admin's intent to restrict swaps to specific counterparties is silently nullified. This is a direct policy bypass on curated pools — an allowed impact under the contest rules.

### Likelihood Explanation
Medium-high. The router is a first-party periphery contract. Any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, at which point the bypass is immediately available to all users. There is no in-protocol warning or guard against this configuration.

### Recommendation
The extension must identify the originating user, not the immediate caller. Two sound approaches:

1. **Extension-data forwarding**: The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This requires a trusted router convention and a matching extension decode.
2. **Separate router allowlist slot**: Introduce a `allowedRouter` mapping. When `sender` is a known router, decode the originating user from `extensionData` and check `allowedSwapper[pool][originatingUser]`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true  // admin adds router to support alice's router swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  pool.swap(msg.sender=router) fires
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true
  → swap succeeds for bob, allowlist bypassed
```

Direct assertion: `allowedSwapper[pool][bob] == false` yet bob's router-mediated swap succeeds because the hook checks `allowedSwapper[pool][router]` instead.

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
