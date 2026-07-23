### Title
`SwapAllowlistExtension` Checks Router Identity Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual end user. If the pool admin allowlists the router so that legitimate users can reach the pool via the router, every unprivileged address can bypass the allowlist by routing through the same router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` as `msg.sender`: [3](#0-2) 

The pool therefore passes `msg.sender = router` as `sender` to `_beforeSwap`: [4](#0-3) 

The allowlist check resolves to `allowedSwapper[pool][router]`. The actual end user's address is never inspected.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → all allowlisted users are also blocked from using the router.
- **Allowlist the router** → every unprivileged address can bypass the allowlist by routing through the router.

The same structural problem exists for multi-hop `exactInput` and `exactOutput` paths, where intermediate hops are called from inside `metricOmmSwapCallback` with `msg.sender = router` again. [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which addresses may swap against a pool. A complete bypass means:

- Any unprivileged user can swap against a pool that was intended to be restricted to specific counterparties (e.g., KYC'd users, institutional market makers, or whitelisted protocols).
- LP funds in such pools are exposed to unauthorized arbitrage, MEV extraction, or adversarial swaps that the allowlist was designed to prevent.
- The pool admin's access-control invariant — "only allowlisted addresses may swap" — is silently broken without any on-chain signal.

This is a direct loss-of-LP-principal risk for any pool that relies on `SwapAllowlistExtension` for security rather than convenience.

---

### Likelihood Explanation

The trigger condition is that the pool admin allowlists the router address. This is a natural and expected operational step: allowlisted users need the router to perform multi-hop swaps, to use slippage protection, or to interact via standard tooling. The router is a public, permissionless contract. Once the router is allowlisted, any address can exploit the bypass with a single `exactInputSingle` call. No privileged access, no special setup, and no malicious token behavior is required.

---

### Recommendation

The extension must verify the identity of the actual end user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to populate the field honestly, which is acceptable since the router is a known, audited contract.

2. **Separate the allowlist key from `sender`**: Introduce a dedicated `realSwapper` field in the extension interface (or a side-channel via transient storage) so the pool can propagate the original EOA through the router hop.

Until fixed, document clearly that `SwapAllowlistExtension` only gates direct pool callers and does not protect against router-mediated swaps.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allow userA to use the router
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           zeroForOne: true,
           amountIn: X,
           ...
       })
5. Router calls pool.swap(...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true  → passes.
8. userB's swap executes against LP funds — allowlist fully bypassed.
``` [6](#0-5) [7](#0-6) [2](#0-1)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
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
