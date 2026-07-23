### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the natural action to permit router-mediated swaps), every unprivileged user can bypass the per-user allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the router is `msg.sender` at the pool boundary: [4](#0-3) 

The identity mismatch is structural: the allowlist is configured with end-user addresses, but the hook sees the router address. The admin has two bad options:

1. **Do not allowlist the router** → every allowlisted user is blocked from using the router (broken UX).
2. **Allowlist the router** → the per-user gate is completely open; any address can call `exactInputSingle` through the public router and swap on the restricted pool.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` argument (the LP position owner), not on `sender`: [5](#0-4) 

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., a private OTC pool or a KYC-gated venue). Once the admin allowlists the router to support normal UX, the restriction is void: any address calls `MetricOmmSimpleRouter.exactInputSingle`, the pool sees `sender = router`, the router is allowlisted, and the swap executes. Non-allowlisted users drain liquidity from a pool that was designed to exclude them, causing direct loss of LP principal and fee revenue under conditions the pool admin explicitly tried to prevent.

---

### Likelihood Explanation

The router is a public, permissionless contract. Allowlisting it is the expected operational step for any pool that wants to support standard swap UX. The bypass requires no special privilege, no flash loan, and no unusual token behavior — a single `exactInputSingle` call from any EOA suffices. The misconfiguration is latent in the design and will be triggered the moment the admin enables router access.

---

### Recommendation

Pass the economically relevant actor — the end user — as `sender` to the extension hooks, not the immediate `msg.sender` of the pool call. Two concrete approaches:

1. **Caller-forwarding in the router**: encode the original `msg.sender` inside `extensionData` and have the extension decode and verify it (requires a trusted router check inside the extension).
2. **Explicit `swapper` parameter on `pool.swap`**: add a `swapper` address parameter that the pool passes to hooks, distinct from the callback payer (`msg.sender`). The router sets `swapper = msg.sender` (the end user); direct callers set `swapper = address(0)` to fall back to `msg.sender`.

Option 2 is cleaner and consistent with how `addLiquidity` already separates `sender` (the payer/caller) from `owner` (the gated identity).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)  // admin enables router for UX

Attack (executed by bob, who is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      <restricted pool>,
      recipient: bob,
      ...
  })

  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives tokens from the restricted pool

Result:
  bob, a non-allowlisted address, successfully swaps on a pool
  that was configured to exclude him, bypassing the guard entirely.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
