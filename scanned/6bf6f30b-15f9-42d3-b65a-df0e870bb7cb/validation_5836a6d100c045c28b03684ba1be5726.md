### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged swapper to bypass a curated pool's allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the **router address**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on the network, defeating the curation policy entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever `msg.sender` the pool received when `swap` was called on it.

In `MetricOmmPool.swap`, `sender` is bound to `msg.sender` of the pool call and forwarded verbatim to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` of the pool's `swap` call is the **router contract address**, not the end user. The extension checks `allowedSwapper[pool][routerAddress]`, not `allowedSwapper[pool][endUser]`.

The router stores the end user's address only in transient storage for the payment callback — it is never surfaced to the extension as `sender`. There is no mechanism in the standard extension interface for the extension to recover the true end user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the restricted pool.
2. The extension sees `sender = routerAddress`, which is allowlisted.
3. The swap executes, draining LP-owned tokens at oracle prices to an unauthorized counterparty.

LP funds are directly at risk: the pool was deployed with the expectation that only vetted counterparties would trade against it, but the allowlist is silently bypassed for all router users.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any pool that wants to support the standard periphery swap path. A pool admin who deploys a curated pool and then enables router support by allowlisting `MetricOmmSimpleRouter` will unknowingly open the pool to all users. The router is a public, permissionless contract. No special privilege or exploit is required beyond calling the router's standard `exactInputSingle` or `exactInput` entry points.

---

### Recommendation

The extension must gate on the **economic actor** — the address that controls the input tokens and benefits from the swap — not the immediate caller of the pool. Two approaches:

1. **Pass end-user identity through `extensionData`**: require the router to encode the originating user in `extensionData` and have the extension verify it (with a signature or factory-registered router registry).
2. **Registry-aware check**: maintain a factory-registered set of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

The simpler short-term fix is to document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the per-user allowlist illusion, or to prohibit allowlisting known router addresses.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router to support standard periphery swaps.
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true); // ← natural config

// Attack: bob (not allowlisted) swaps through the router.
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);

// The extension sees sender = address(router), which is allowlisted → passes.
// Bob receives token0 from the pool despite not being on the allowlist.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: address(token1),
        recipient: bob,
        deadline: block.timestamp + 1,
        amountIn: 10_000,
        amountOutMinimum: 0,
        zeroForOne: false,
        priceLimitX64: type(uint128).max,
        extensionData: ""
    })
);

// bob received token0 from a pool he was never allowlisted on.
assertGt(token0.balanceOf(bob), 0);
vm.stopPrank();
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-85)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
