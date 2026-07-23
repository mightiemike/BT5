### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the natural step to let their approved users trade via the standard periphery), every unprivileged user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument of the `beforeSwap` ABI call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` as `msg.sender`: [4](#0-3) 

So the extension receives `sender = router_address`, not the end user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**The bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin allowlists specific user addresses (`alice`, `bob`).
3. Admin also allowlists the router address so that `alice` and `bob` can use the standard periphery.
4. `charlie` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
5. The router calls `pool.swap(...)` — the extension sees `sender = router`, which is allowlisted.
6. `charlie`'s swap executes successfully, bypassing the allowlist entirely.

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` argument (the position owner), which the pool passes independently of `msg.sender`, so the liquidity adder path correctly gates the economic beneficiary: [5](#0-4) 

The swap extension has no equivalent separation — it only has `sender`, which collapses to the router for all router-mediated swaps.

---

### Impact Explanation

Any user can execute swaps on a pool whose admin intended to restrict access to a curated set of addresses. The attacker does not need any special privilege — only access to the public `MetricOmmSimpleRouter`. LP funds in the curated pool are exposed to unauthorized counterparties. If the pool was designed for a specific risk profile (e.g., institutional-only, KYC-gated), the allowlist provides zero protection against router-mediated swaps once the router is allowlisted. This is a direct admin-boundary break reachable by any unprivileged user through a supported production entrypoint.

---

### Likelihood Explanation

The router is the standard, documented way for end users to interact with pools. A pool admin who wants their allowlisted users to be able to use the router **must** allowlist the router address — there is no other mechanism. The moment they do so, the allowlist is fully bypassed for all router users. This is not a misconfiguration edge case; it is the expected operational path for any curated pool that supports periphery access.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor** (the end user), not the immediate caller of `pool.swap()`. Two approaches:

1. **Preferred — check `recipient` as a proxy for the end user when `sender` is a known router.** This requires the extension to maintain a registry of trusted routers and fall back to checking `recipient` when `sender` is a router. However, `recipient` can be set to any address by the router caller, so this is not fully sound either.

2. **Correct — require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` only enforces correctly when users call `pool.swap()` directly, and that allowlisting the router defeats the guard. Emit a warning or revert if `sender` is a registered router.

3. **Structural fix — pass the original `msg.sender` (end user) through the router as an additional field in `extensionData`, and have the extension decode and verify it.** This requires a coordinated change to the router and extension interface.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)
  - Admin calls setAllowedToSwap(pool, router, true)   // needed so alice can use the router

Attack:
  - charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: curated_pool,
          recipient: charlie,
          ...
      })
  - Router calls pool.swap(charlie, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; charlie receives output tokens from the curated pool

Result:
  - charlie bypassed the allowlist entirely
  - allowedSwapper[pool][charlie] was never checked
```

The root cause is at `SwapAllowlistExtension.sol:37` where `sender` (the router) is checked instead of the actual end user, and at `MetricOmmSimpleRouter.sol:71–80` where no end-user identity is forwarded to the pool or extension layer. [6](#0-5) [7](#0-6)

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
