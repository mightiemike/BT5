### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` from the pool's perspective — the router contract — not the end user. When the pool admin allowlists the `MetricOmmSimpleRouter` (required for router-mediated swaps to work for legitimate users), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes `msg.sender` in the pool: [4](#0-3) 

So the extension receives `sender = address(router)`, not the end user's address. The allowlist check passes if the router is allowlisted, regardless of who called the router.

The pool admin faces an impossible choice:
- **Allowlist the router** → every user can bypass the allowlist via the router.
- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned contracts) can be freely accessed by any address through the public router. This breaks the core access-control invariant the extension is designed to enforce, exposing the pool to toxic flow, unauthorized arbitrage, or any other harm the allowlist was intended to prevent. LP assets and protocol fees in the restricted pool are at risk from actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The scenario is directly reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool that wants to support router-mediated swaps for its legitimate users. The `MetricOmmSimpleRouter` is a public, permissionless contract, so no special access is required to trigger the bypass.

---

### Recommendation

Pass the original end-user address through the call chain rather than the immediate `msg.sender`. One approach: have the router store the originating user in transient storage (as it already does for the payer in `_setNextCallbackContext`) and expose it via a getter that the extension can read from `msg.sender` (the pool's caller). Alternatively, the pool can accept an explicit `swapper` parameter distinct from `msg.sender` and validate it against a router whitelist before forwarding to extensions. The `DepositAllowlistExtension` avoids this problem by gating on `owner` (the position owner explicitly supplied by the caller) rather than `sender`; the swap path needs an equivalent separation. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for legitimate users
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  4. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
     → router calls pool.swap(recipient, ..., extensionData)
     → pool calls _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true  → check passes
     → swap executes for attacker with no allowlist enforcement
```

The attacker successfully swaps on a pool that should have blocked them, with the pool receiving no indication that the actual swapper was unauthorized.

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
