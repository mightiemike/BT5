### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass the swap allowlist when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router address (a natural step to let allowlisted users access multi-hop or WETH-unwrapping flows), every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Actor binding in the extension:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever `MetricOmmPool.swap()` received as its own `msg.sender`.

**What the pool passes:**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

**What the router passes:**

For every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

So `pool.swap()` sees `msg.sender = router`, and the extension receives `sender = router`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The bypass path:**

A pool admin who wants allowlisted users to access multi-hop swaps or WETH-unwrapping (both require the router) must allowlist the router address:

```
allowedSwapper[pool][router] = true
```

Once that entry exists, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the end user is. Any unprivileged address can call `router.exactInputSingle(...)` and the extension check passes unconditionally.

The admin has no way to express "only these specific users may swap through the router" with the current extension design. The only choices are:
1. Do not allowlist the router → allowlisted users cannot use the router at all.
2. Allowlist the router → every user bypasses the allowlist via the router.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` is a curated pool: only specific addresses are supposed to trade against its liquidity. LPs deposit expecting only those counterparties. Once the admin allowlists the router (a necessary step for allowlisted users to access multi-hop or WETH flows), any address can trade against the pool's liquidity at oracle-derived prices. This constitutes:

- **Direct LP loss**: LPs bear adverse selection from counterparties they never consented to trade with.
- **Allowlist policy failure**: The curation invariant ("only allowlisted addresses may swap") is broken for all router-mediated swaps.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical periphery entry point for swaps; most users and integrations will use it.
- A pool admin who wants allowlisted users to access multi-hop paths or WETH unwrapping has no alternative but to allowlist the router.
- Once the router is allowlisted, the bypass requires zero special knowledge: any user calls `exactInputSingle` on the router pointing at the curated pool.
- No admin key, no privileged role, and no special token behavior is required by the attacker.

---

### Recommendation

The extension must gate the end user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: Have the router encode the original `msg.sender` into `extensionData` for each hop, so extensions can decode and verify the true initiator.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the end-user address from `extensionData` when `sender` is a known router, and fall back to checking `sender` directly for non-router callers. Alternatively, maintain a separate `allowedRouter` mapping and require that the router itself passes the end-user address in a verified way.

Until this is resolved, pool admins should be warned that allowlisting the router address opens the pool to all users.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice = allowlisted user
  bob   = non-allowlisted user
  admin allowlists router so alice can use multi-hop swaps:
    swapExtension.setAllowedToSwap(pool, router, true)

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          // msg.sender = router
  → pool calls extension.beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob on the curated pool
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
