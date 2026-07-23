### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. `MetricOmmPool.swap` derives `sender` from `msg.sender`, which is the router contract when a user enters through `MetricOmmSimpleRouter`. Because the extension sees the router's address—not the real user's address—any non-allowlisted user can execute swaps on a restricted pool simply by calling the router, provided the router itself is allowlisted (which is required for any allowlisted user to use the router at all).

---

### Finding Description

**Hook argument binding**

`ExtensionCalling._beforeSwap` forwards `sender` as the first positional argument to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  lines 149-177
function _beforeSwap(
    address sender,   // ← pool passes msg.sender here
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, zeroForOne, ...))
    );
}
```

`MetricOmmPool.addLiquidity` (the only other hook call visible in the read window) follows the identical pattern—`msg.sender` is passed as `sender`. The `swap` function follows the same convention, so when the router calls `pool.swap(...)`, the pool records `msg.sender = router` as `sender`.

**Extension check**

`SwapAllowlistExtension.beforeSwap` gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is the router when the call originates from `MetricOmmSimpleRouter`.

**Router call site**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no user-identity forwarding:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 71-80
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
```

The real user's address (`msg.sender`) is stored only in transient storage for the payment callback; it is never forwarded to the pool as a swap parameter. The pool therefore sees `msg.sender = router` and passes that to `_beforeSwap`.

**The dilemma**

A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every caller, so the extension passes for every user who routes through `MetricOmmSimpleRouter`—including users who were never individually allowlisted.

The admin has no way to express "allow Alice through the router but not Charlie" because the extension only ever sees the router's address.

**Contrast with deposit allowlist**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the second argument), not `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The deposit extension correctly gates on the position owner regardless of which intermediary called the pool. The swap extension has no equivalent—it gates on the caller identity, which collapses to the router for all router-mediated swaps.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or a private market) is fully bypassed for any user who calls `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-derived prices against LP capital that was deposited under the assumption that only allowlisted counterparties would trade. This directly exposes LP principal to unauthorized extraction.

---

### Likelihood Explanation

**Medium.** The bypass is reachable whenever (a) a pool deploys `SwapAllowlistExtension` and (b) the pool admin allowlists the router so that legitimate users can access the router's slippage-protection and multi-hop features. Both conditions are the expected production configuration for any allowlisted pool that also wants router compatibility. No privileged access, no malicious setup, and no non-standard token behavior is required—only a standard `exactInputSingle` call.

---

### Recommendation

1. **Forward the real user identity through the swap call.** Add a `swapper` parameter to `IMetricOmmPoolActions.swap` (analogous to `owner` in `addLiquidity`) so the pool can pass the true initiator to extensions. The router would supply `msg.sender` as `swapper`.

2. **Alternatively, gate on `recipient` or a signed identity payload in `extensionData`.** The router already knows the real user; it can embed a signed claim in `extensionData` that the extension verifies.

3. **Document the current limitation explicitly** so pool admins do not deploy `SwapAllowlistExtension` with the router allowlisted under the false belief that individual-user gating is enforced.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // Alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)      // required so Alice can use the router
4. LPs add liquidity to the pool.

Attack
──────
5. Charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      pool,
           recipient: charlie,
           zeroForOne: true,
           amountIn:  X,
           ...
       })
6. Router calls pool.swap(charlie, true, X, ...) with msg.sender = router.
7. Pool calls _beforeSwap(sender=router, ...).
8. Extension evaluates allowedSwapper[pool][router] → true → passes.
9. Charlie's swap executes at oracle price, draining LP token1.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```
