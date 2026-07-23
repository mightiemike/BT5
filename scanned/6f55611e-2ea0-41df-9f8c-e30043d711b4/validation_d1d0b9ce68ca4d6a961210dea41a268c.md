### Title
SwapAllowlistExtension Checks Router Identity Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the pool admin allowlists the router (the natural configuration for pools that want to support router-mediated swaps), any unprivileged user can bypass the allowlist entirely by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` of its own `swap()` call as `sender`:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, amountSpecified, ...);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

At the point the pool calls `_beforeSwap`, `msg.sender` of the pool's `swap()` is the **router**, not the original user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** user who routes through it, regardless of whether that user is individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner explicitly passed to `addLiquidity`), which is preserved correctly through the liquidity adder path. [4](#0-3) 

---

### Impact Explanation

Any user can swap on a curated pool that is supposed to be restricted to allowlisted addresses only, by routing through the public `MetricOmmSimpleRouter`. The swap allowlist — the primary access-control mechanism for curated pools — is rendered completely ineffective for router-mediated swaps. This is a direct loss of the pool's curation guarantee and allows unauthorized parties to extract liquidity from pools that were designed to serve only specific counterparties (e.g., KYC'd users, institutional partners).

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router. This is the natural and expected configuration: a pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router. There is no other way to enable router support while keeping the allowlist active. The exploit is therefore reachable in any production deployment of a curated pool that supports the standard periphery router.

---

### Recommendation

The `SwapAllowlistExtension` should not check the intermediary caller (`sender = router`) but the economically relevant actor. Two approaches:

1. **Check `msg.sender` of the extension call (the pool) against a per-pool allowlist keyed by the original user.** This requires the pool to forward the original user identity separately (e.g., via `extensionData`), which is a protocol-level change.

2. **Require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that router-mediated swaps are incompatible with user-level allowlisting. Enforce this at the factory level or via a dedicated router that forwards the original caller identity.

The simplest short-term fix is to add a note in the extension and factory that allowlisting the router on a `SwapAllowlistExtension`-gated pool defeats the allowlist, and to provide a router variant that passes the original `msg.sender` through `extensionData` so the extension can check it.

---

### Proof of Concept

**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted to support router-mediated swaps for Alice.

**Exploit (Bob, not allowlisted):**
```solidity
// Bob calls the public router directly
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(curated_pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
```

**Trace:**
- `router.exactInputSingle()` → `pool.swap(recipient=bob, ...)` with `msg.sender = router`
- Pool calls `_beforeSwap(sender=router, ...)`
- Extension checks `allowedSwapper[pool][router]` → `true`
- Bob's swap succeeds despite not being individually allowlisted [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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
