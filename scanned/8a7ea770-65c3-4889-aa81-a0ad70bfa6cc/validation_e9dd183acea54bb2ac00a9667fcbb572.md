### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the actual end user. Any non-allowlisted user can therefore bypass a curated pool's swap allowlist by calling the router, provided the router address is itself allowlisted (which is the only way to enable router-mediated swaps on such a pool).

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of the pool
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

At this point `msg.sender` inside the pool is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to permit router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every user** — including those the admin explicitly never allowlisted — can call `router.exactInputSingle()` and the extension will pass them through, because it only sees the router address.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` argument (the position owner), which the `MetricOmmPoolLiquidityAdder` correctly forwards as the actual depositor, not the adder contract itself.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for the router path. Any unprivileged user can execute swaps against the pool's liquidity, draining LP value through arbitrage or directional trading that the allowlist was intended to prevent. This is a direct loss of LP principal and a complete failure of the configured access-control boundary.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user who discovers that the router is allowlisted on a curated pool can exploit this immediately with a single `exactInputSingle` call. No privileged access, no special setup, and no multi-step sequence is required. The pool admin is forced into an impossible choice: either allowlist the router (opening the bypass) or block it (making the router unusable for their legitimate users).

---

### Recommendation

Pass the **original user** through the swap path so the extension can gate on the economically relevant actor. One approach: have the router store the real `msg.sender` in transient storage and expose it via a callback or a dedicated getter that the pool reads before invoking `_beforeSwap`. Alternatively, redesign `SwapAllowlistExtension` to accept an explicit `user` field inside `extensionData` that the router populates and signs, and verify it inside the hook. The invariant that must hold is: the identity checked by the allowlist must be the address that controls the funds and initiates the trade, not the intermediate contract that relays the call.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension wired as beforeSwap hook.
2. Pool admin allowlists alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin also allowlists the router so alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)

Attack
──────
4. charlie (never allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: charlie, ...})
5. Router calls pool.swap(); msg.sender inside pool = router.
6. _beforeSwap(sender=router, ...) is dispatched.
7. Extension evaluates allowedSwapper[pool][router] == true → passes.
8. Swap executes; charlie receives tokens from the curated pool.

Expected: revert NotAllowedToSwap (charlie is not allowlisted).
Actual:   swap succeeds; allowlist is fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
