### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` is used, `msg.sender` of the pool's `swap` is always the router contract, not the originating user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when routing
    recipient,
    ...
);
```

`MetricOmmSimpleRouter` calls `pool.swap(...)` directly for every function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`):

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

In every case, `msg.sender` of the pool's `swap` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all — the extension reverts `NotAllowedToSwap` for every router-mediated swap.
- **If the router IS allowlisted**: the allowlist is completely bypassed — any unprivileged user can call `exactInputSingle` (or any other router function) and the extension passes because `allowedSwapper[pool][router] == true`.

The `DepositAllowlistExtension` does not share this flaw because it ignores `sender` and checks `owner` (the position owner), which is correctly forwarded regardless of who calls `addLiquidity`. The swap path has no equivalent owner concept — the economically relevant actor is the originating user, but only the router's address reaches the extension.

---

### Impact Explanation

A pool that deploys `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-verified traders, institutional partners, or whitelisted arbitrageurs) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The practical consequences are:

- **LP principal loss via adverse selection**: unauthorized arbitrageurs can drain mispriced bins that the allowlist was intended to protect.
- **Protocol fee leakage**: swap fees accrue from unauthorized volume the pool was not designed to accept.
- **Compliance failure**: pools deployed under regulatory constraints (KYC/AML) are exposed to unrestricted public access.

The loss is bounded only by pool liquidity depth and oracle price accuracy, not by any on-chain cap.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural and expected action, since without it no allowlisted user can use the router. The admin has no way to simultaneously allow their approved users to route through `MetricOmmSimpleRouter` and block unapproved users from doing the same. Any pool that enables the router for its approved users is immediately exploitable by any address. No special privileges, flash loans, or oracle manipulation are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The pool must forward the originating user's address to the extension, not the immediate `msg.sender`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the originating user in `extensionData` and the extension should decode it. This requires a convention between router and extension.

2. **Extension-side (preferred)**: Add a `recipient`-aware or `extensionData`-decoded identity check. The router already stores the originating payer in transient storage (`_getPayer()`); it can encode that address into `extensionData` so the extension can verify the real actor.

3. **Pool-level alternative**: Expose a separate `swapOnBehalf(address realUser, ...)` entry point that passes `realUser` as `sender` to extensions, callable only by allowlisted routers.

Until fixed, pools that need per-user swap gating must not allowlist `MetricOmmSimpleRouter` and must instruct users to call `pool.swap` directly.

---

### Proof of Concept

```solidity
// Setup: pool has SwapAllowlistExtension; only `alice` is allowlisted; router is allowlisted
// so alice can use the router.

address router = address(metricOmmSimpleRouter);
swapAllowlist.setAllowedToSwap(address(pool), router, true);   // admin must do this for alice to route
swapAllowlist.setAllowedToSwap(address(pool), alice, true);    // alice is the intended grantee

// Attack: bob (not allowlisted) routes through the router
vm.prank(bob);
token0.approve(address(router), type(uint256).max);

IMetricOmmSimpleRouter.ExactInputSingleParams memory params = IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    deadline: block.timestamp + 1,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    extensionData: ""
});

vm.prank(bob);
// Succeeds: extension checks allowedSwapper[pool][router] == true, not allowedSwapper[pool][bob]
uint256 amountOut = router.exactInputSingle(params);
assertGt(amountOut, 0); // bob swapped successfully despite not being allowlisted
```

The extension receives `sender = address(router)`, finds it allowlisted, and permits the swap. Bob's address is never evaluated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
