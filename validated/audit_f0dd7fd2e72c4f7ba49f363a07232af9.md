### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When `MetricOmmSimpleRouter` intermediates the call, `sender` becomes the router address, not the actual end user. A pool admin who allowlists the router (a natural operational step) inadvertently grants every user access to a curated pool, bypassing the per-address allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument of `IMetricOmmExtensions.beforeSwap`. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user. [4](#0-3) 

Therefore the allowlist check becomes `allowedSwapper[pool][router]`. The actual end user's address is never inspected.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly ignores `sender` (the operator/payer) and gates on `owner` (the position beneficiary): [5](#0-4) 

No equivalent "owner" concept exists for swaps, so `SwapAllowlistExtension` has no way to recover the true end user from the arguments the pool provides.

---

### Impact Explanation

A pool admin who wants to allow router-based swaps will call `setAllowedToSwap(pool, routerAddress, true)`. From that point, **any address** — including addresses explicitly not allowlisted — can call `router.exactInputSingle(...)` and the extension will pass because it sees `sender = router`, which is allowlisted. The curated-pool invariant (only approved counterparties may trade) is broken. Disallowed users can execute swaps, draining pool liquidity at oracle prices, which constitutes direct loss of LP assets and a curation failure above Sherlock thresholds.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to have allowlisted the router address, which is a natural and expected operational step for any pool that intends to support the standard periphery. The admin has no indication from the contract or its documentation that doing so opens the allowlist to all users. Once the router is allowlisted, any unprivileged user can exploit it without further preconditions.

---

### Recommendation

The extension must identify the true end user, not the intermediary. Two viable approaches:

1. **Decode from `extensionData`**: Require callers (router included) to ABI-encode the real user address in `extensionData`. The extension decodes and checks that address. The router already threads `extensionData` through unchanged, so this is backward-compatible.

2. **Check `recipient` instead of `sender`**: For swap allowlists the `recipient` (second argument to `beforeSwap`) is often the true economic beneficiary. If the pool's policy is to gate by recipient, switch the check to that field. This does not help when `recipient` is also an intermediary.

3. **Document the incompatibility**: If neither approach is taken, the extension's NatSpec must explicitly state it is incompatible with any router or multicall intermediary, and the factory should enforce this at pool creation time.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, routerAddress, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           tokenIn: token0,
           ...
       })
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...).
     Pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. Extension evaluates:
       allowedSwapper[pool][router] == true  →  passes.
  5. Swap executes. attacker receives token1 output.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; curated pool allowlist is bypassed.
``` [6](#0-5) [7](#0-6)

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
