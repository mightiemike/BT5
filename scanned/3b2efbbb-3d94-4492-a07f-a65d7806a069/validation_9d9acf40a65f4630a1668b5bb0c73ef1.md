### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is always `msg.sender` of the pool's `swap()` call. When a user enters through the public `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (the only way to let legitimate users use the router), every non-allowlisted address can bypass the gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to every configured `beforeSwap` hook:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The actual end-user's address is **never forwarded** to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants legitimate users to trade through the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including non-allowlisted addresses. The allowlist is fully bypassed.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks the `owner` argument (the position owner), not `sender` (the direct caller), so the deposit gate is not affected by the same flaw:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

The swap extension has no equivalent "owner" concept to fall back on; the pool's `swap()` interface carries no caller-identity field beyond `msg.sender`.

---

### Impact Explanation

Any non-allowlisted address can trade on a curated pool that has allowlisted the router, bypassing the admin-configured access control. Curated pools are typically restricted to prevent unauthorized extraction of LP value, front-running by untrusted counterparties, or regulatory non-compliance. A successful bypass lets an attacker execute swaps against the pool's liquidity under conditions the LP depositors never consented to, constituting a direct admin-boundary break with potential LP fund impact.

---

### Likelihood Explanation

The bypass is triggered only when the pool admin has allowlisted the router address. This is the natural configuration for any curated pool whose legitimate users are expected to interact through the standard periphery. A pool admin who allowlists only raw EOA addresses (forcing direct pool calls) avoids the issue, but this makes the router unusable for those users. The likelihood is **medium**: the misconfiguration is the expected production path for router-accessible curated pools.

---

### Recommendation

1. **Short-term**: Document explicitly that `SwapAllowlistExtension` cannot correctly gate router-mediated swaps. Pool admins must never allowlist the router address; instead, they must require direct pool calls from allowlisted EOAs.
2. **Long-term**: Redesign the swap allowlist to check an identity that survives router indirection. Options include:
   - Requiring the router to embed the actual user's address in `extensionData` and verifying it with a signature inside the extension.
   - Adding an explicit `swapper` field to the pool's `swap()` interface (analogous to `owner` in `addLiquidity`), so the extension can check the intended beneficiary rather than the direct caller.

---

### Proof of Concept

```
1. Pool admin deploys a curated pool with SwapAllowlistExtension configured.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true);
3. Non-allowlisted attacker (address X, not in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, recipient: X, ...});
4. Router calls pool.swap() — msg.sender to the pool is the router.
5. Pool calls _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] → true.
6. Swap executes. Attacker receives output tokens from the curated pool's LP reserves.
   The allowlist never evaluated address X.
``` [5](#0-4) [1](#0-0) [6](#0-5)

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
