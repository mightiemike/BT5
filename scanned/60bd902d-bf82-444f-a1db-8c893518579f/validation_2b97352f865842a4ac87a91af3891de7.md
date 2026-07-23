### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (a necessary step for any allowlisted user to use the router), every unprivileged user can bypass the swap allowlist by calling the router, executing unauthorized swaps against the pool.

---

### Finding Description

**Inconsistency between the two allowlist extensions:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` argument — the actual position beneficiary — regardless of who the intermediary caller is: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` instead gates the `sender` argument, which the pool binds to `msg.sender` of the `swap()` call: [2](#0-1) 

The pool always passes `msg.sender` as `sender` to the hook: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is used, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The trap for pool admins:**

A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. The moment they do, `allowedSwapper[pool][router] == true`, and the check at line 37 of `SwapAllowlistExtension` passes for every caller — allowlisted or not — because the router is the entity the extension sees. [5](#0-4) 

---

### Impact Explanation

Any unprivileged user can execute swaps against a pool that has `SwapAllowlistExtension` configured, as long as the router is allowlisted. The allowlist is intended to restrict which addresses may trade against the pool's LP reserves. Bypassing it allows unauthorized parties to execute swaps, draining LP funds at oracle-determined prices without the pool admin's consent. This breaks the core access-control invariant of the extension and constitutes a direct loss path for LP principal.

---

### Likelihood Explanation

The likelihood is high. The router is the standard user-facing swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and wants their approved users to be able to use the router must allowlist the router. This is the natural and expected operational step. Once taken, the bypass is open to every address with no further preconditions.

---

### Recommendation

Gate the actual end user, not the intermediary. Two options:

1. **Check `sender` only for direct pool calls; require the router to forward the real user identity.** The router could pass the original `msg.sender` as a verified field in `extensionData`, and the extension could decode and verify it (with a router-signed proof or a trusted-router registry).

2. **Mirror the deposit allowlist pattern.** For swaps, the economically relevant actor is the address that receives the output (`recipient`). The extension could check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`, which is invariant to router intermediation (the router always forwards the caller-supplied `recipient` unchanged). [6](#0-5) 

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended gated user
  allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker] = false      // attacker is NOT allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
  })

  Router calls pool.swap(attacker, ...)
  Pool calls _beforeSwap(msg.sender=router, recipient=attacker, ...)
  Extension checks: allowedSwapper[pool][router] == true  → PASSES
  Swap executes; attacker receives output tokens from LP reserves.

Result:
  attacker bypasses the swap allowlist and executes an unauthorized swap.
  DepositAllowlistExtension would NOT have this problem because it checks
  `owner` (the beneficiary), not `sender` (the intermediary).
``` [2](#0-1) [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

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
