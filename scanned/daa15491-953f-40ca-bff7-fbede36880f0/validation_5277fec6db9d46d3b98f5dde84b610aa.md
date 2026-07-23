### Title
SwapAllowlistExtension Checks Router Address as Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If a pool admin allowlists the router address (the natural action to enable allowed users to use the router), every disallowed user can bypass the allowlist by routing through the router.

---

### Finding Description

**Hook argument binding — `sender` is the direct pool caller, not the end user.**

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to every `beforeSwap` extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [3](#0-2) 

So the pool passes `msg.sender = router` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]` — the router's allowlist status — not the end user's.

**The bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses.
2. Pool admin allowlists the router address so that allowed users can trade through the standard periphery entry point (the natural operational step).
3. Any disallowed user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
4. The pool passes `sender = router` to the extension; the extension sees `allowedSwapper[pool][router] == true` and passes.
5. The disallowed user's swap executes against the restricted pool.

The same bypass applies to all four router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

**High.** A disallowed user can trade on a curated/restricted pool, fully bypassing the intended access control. The pool receives tokens and pays out tokens to the attacker's `recipient` address. Any economic restriction the pool admin intended to enforce (KYC gating, whitelist-only liquidity pools, partner-only pricing) is nullified. The attacker receives real token output from the pool at the oracle-driven price, constituting a direct policy bypass with fund-level consequences on every restricted pool that has allowlisted the router.

---

### Likelihood Explanation

**Medium.** The router is the primary user-facing entry point for swaps. A pool admin who wants allowed users to be able to use the router will naturally allowlist the router address. The admin has no indication from the contract or documentation that doing so opens the allowlist to all users. The bypass requires only a standard router call — no special privileges, no flash loans, no multi-step setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end user's identity**, not the direct pool caller. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` against the router and fall back to `tx.origin` or a forwarded identity**: Not recommended due to `tx.origin` risks.

3. **Preferred — gate on `recipient` or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps unless the router itself is the intended gated entity.

The cleanest fix is to have the router forward the original caller's address in `extensionData` and have the extension decode it when `sender` is a known router:

```diff
// SwapAllowlistExtension.beforeSwap
- address swapper = sender;
+ address swapper = (sender == ROUTER) ? abi.decode(extensionData, (address)) : sender;
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
```

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap order set)
  - allowedSwapper[pool][alice] = true          // alice is allowed
  - allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it
  - allowedSwapper[pool][attacker] = false      // attacker is NOT allowed

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: restrictedPool,
      recipient: attacker,
      ...
  })

  → router calls pool.swap(attacker, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
  → swap executes, attacker receives token output

Result:
  Disallowed attacker successfully trades on a restricted pool.
  The allowlist is completely bypassed.
``` [2](#0-1) [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-125)
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

  /// @inheritdoc IMetricOmmSimpleRouter
  /// @dev Walks `pools[0..n-1]` forward. Each hop swaps a positive `amountSpecified`; the prior hop's output
  ///      becomes the next hop's input. Intermediate tokens stay on this contract; the final hop sends output to
  ///      `recipient`.
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
