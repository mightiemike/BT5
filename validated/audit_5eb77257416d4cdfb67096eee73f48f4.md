### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those the allowlist was designed to block — can bypass the gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument of the `beforeSwap` ABI call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The result is a structural identity collapse: the extension sees `sender = router` for every user who goes through the router. The pool admin faces an impossible choice:

| Admin configuration | Effect |
|---|---|
| Allowlist specific user addresses | Those users are blocked when they use the router (router address not allowlisted) |
| Allowlist the router address | Every user on-chain can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps for approved users and blocks router-mediated swaps for unapproved users.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, institutional market makers, or whitelisted protocols) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP liquidity. LP providers who deposited under the assumption that only approved counterparties could trade against them are exposed to unapproved flow, which can cause direct LP principal loss through adverse selection or unauthorized fee extraction.

**Severity**: High — broken core allowlist functionality with direct LP-principal exposure on any curated pool that uses the router.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants to support standard UX must allowlist the router, which is the exact configuration that triggers the bypass. The attacker requires no special privilege — a single public router call suffices.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that value. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender` for swap allowlisting**: If the pool's intent is to restrict who *receives* output, `recipient` is already passed to `beforeSwap` and is set by the router to the user-supplied value. For input-side gating, a dedicated caller-identity field in `extensionData` is the cleanest solution.

The `DepositAllowlistExtension` avoids this problem because it checks `owner` (the position owner, explicitly supplied by the caller), not `sender`: [5](#0-4) 

The swap allowlist should adopt an equivalent pattern where the checked identity is the actor the pool admin actually intends to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: attacker,
         ...
       })
  2. Router calls pool.swap(attacker, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  attacker bypassed the allowlist and traded against the curated pool's
  LP liquidity without being an approved swapper.
``` [3](#0-2) [6](#0-5) [2](#0-1)

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
