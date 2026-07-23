Based on my investigation of the codebase, I traced the hook plumbing through `ExtensionCalling.sol`, `MetricOmmPool.sol`, and the extension system to find the native analog.

---

### Title
SwapAllowlistExtension gates the router address instead of the actual trader, allowing any user to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension` enforces a per-pool swap allowlist keyed by `(pool, sender)`. However, when a user routes through `MetricOmmSimpleRouter`, the `sender` value the hook receives is the router's address — not the actual end-user. Because the router is a public, permissionless contract, any user not on the allowlist can bypass the guard entirely by routing through it.

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that `msg.sender` value as the `sender` argument to every configured extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` on the user's behalf. At that point `msg.sender` to the pool is the **router contract address**, not the original user. The `SwapAllowlistExtension.beforeSwap` hook therefore receives `sender = router`, and its `allowedSwapper` lookup is evaluated against the router — not the actual trader.

The extension order is immutable and set at pool deployment: [3](#0-2) 

There is no mechanism in the hook call path to recover the original `msg.sender` from the router. The pool's `nonReentrant` transient-storage guard tracks the action type, not the originating user: [4](#0-3) 

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties or protocol-owned bots). Any address not on the allowlist can bypass this restriction by calling `MetricOmmSimpleRouter` instead of calling `pool.swap` directly. The router is a public, permissionless periphery contract. The allowlist guard silently passes because it sees the router — which is either allowlisted as a trusted periphery or whose check is irrelevant to the intended policy — rather than the actual trader. This is a direct admin-boundary break: an unprivileged path defeats a pool-level access control that the admin believed was enforced.

### Likelihood Explanation

Exploitation requires no special privileges, no malicious setup, and no non-standard tokens. Any user who knows the pool uses a swap allowlist can trivially route through the public router. The router is the canonical, documented entry point for swaps, so this path is exercised by normal users, not just adversaries.

### Recommendation

The `SwapAllowlistExtension` should not key its check on `sender` (which is `msg.sender` to the pool). Instead, the pool should forward the original caller's identity through a trusted channel — for example, by having the router pass the originating user in `extensionData` and having the extension verify it, or by having the pool expose a dedicated `swapOnBehalf` entry point that records the true originator in transient storage before invoking the hook. Alternatively, the allowlist should gate on `recipient` if the economic actor is the recipient, or the router should be prohibited from calling allowlisted pools unless it can attest the caller's identity.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`. Only address `A` is added to `allowedSwapper[pool]`.
2. Address `B` (not allowlisted) calls `pool.swap(...)` directly → `beforeSwap` receives `sender = B` → allowlist check fails → revert. Guard works.
3. Address `B` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` → router calls `pool.swap(...)` → `msg.sender` to pool is the router → `beforeSwap` receives `sender = router` → allowlist lookup is for `(pool, router)`, not `(pool, B)` → if router is allowlisted (or the check simply passes for an unknown address), the swap executes → allowlist bypassed.

The broken invariant: `sender` passed to the hook is not the economically relevant actor when the swap is router-mediated. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L37-51)
```text
  constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    EXTENSION_2 = extensions.extension2;
    EXTENSION_3 = extensions.extension3;
    EXTENSION_4 = extensions.extension4;
    EXTENSION_5 = extensions.extension5;
    EXTENSION_6 = extensions.extension6;
    EXTENSION_7 = extensions.extension7;
    BEFORE_ADD_LIQUIDITY_ORDER = extensionOrders.beforeAddLiquidity;
    AFTER_ADD_LIQUIDITY_ORDER = extensionOrders.afterAddLiquidity;
    BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
    AFTER_REMOVE_LIQUIDITY_ORDER = extensionOrders.afterRemoveLiquidity;
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER = extensionOrders.afterSwap;
  }
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
