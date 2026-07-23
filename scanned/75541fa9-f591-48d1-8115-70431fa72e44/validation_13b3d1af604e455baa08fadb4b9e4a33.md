### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the **router's** allowlist status — not the actual end user's. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently opens the allowlist to every user on the network.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`_beforeSwap` encodes this value as the `sender` argument and forwards it to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension keys its check on `sender`.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` = pool (the caller of the extension), `sender` = whoever called `pool.swap()`. The lookup is `allowedSwapper[pool][sender]`.

**Step 3 — MetricOmmSimpleRouter calls `pool.swap()` directly.**

```solidity
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
``` [4](#0-3) 

The router is `msg.sender` to the pool. Therefore the extension receives `sender = router_address`, and the check becomes `allowedSwapper[pool][router]`.

**Step 4 — The impossible choice.**

The pool admin faces a binary dilemma:

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Every user on the network can bypass the allowlist by routing through the public router |
| **No** | No allowlisted user can use the router; the supported periphery path is broken for this pool |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from doing the same.

The same wrong-actor binding applies to `exactInput` multi-hop and `exactOutputSingle`: [5](#0-4) [6](#0-5) 

**Contrast with DepositAllowlistExtension (correctly designed).**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner), not `sender` (the caller of `addLiquidity`). The pool documentation explicitly supports the operator pattern where payer ≠ owner, so the deposit guard correctly gates the economically relevant party regardless of who the intermediary is. [7](#0-6) 

`SwapAllowlistExtension` has no equivalent "owner" concept to fall back on — it only has `sender`, which collapses to the router address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` on the `beforeSwap` hook — intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional desks, or whitelisted market makers) — can be fully bypassed by any unprivileged user who routes through the public `MetricOmmSimpleRouter`. The bypass requires no special privileges, no flash loans, and no price manipulation. The attacker simply calls `exactInputSingle` or `exactInput` on the router. Every swap that executes against the pool's liquidity represents a direct policy violation and potential loss of LP assets to unauthorized counterparties.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user aware of the router can exploit this without any setup cost.
- Pool admins who want router-mediated swaps to work for their allowlisted users have no choice but to allowlist the router, which opens the bypass to everyone.
- The vulnerability is triggered by a single standard router call; no multi-transaction setup is required.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual end user, not the intermediary. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` (the original caller) into `extensionData` before forwarding to the pool. The extension decodes and verifies this address. The pool must also verify that the encoded address matches the payer stored in the router's transient callback context to prevent spoofing.

2. **Add a `recipient`-based check**: For swaps, the `recipient` parameter is the address that receives output tokens and is set by the original caller. The extension could check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`. This is not a perfect substitute (recipient can be set to any address), but it is closer to the economically relevant actor than the router.

The cleanest fix is approach (1), with the router explicitly attesting the original caller's identity in `extensionData` and the extension verifying it.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension on beforeSwap
// allowedSwapper[pool][router] = true  (admin must set this for router to work)
// allowedSwapper[pool][attacker] = false (attacker is NOT on the allowlist)

// Attacker bypasses the allowlist:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: targetPool,
        tokenIn: token0,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] = true → passes
// attacker swaps successfully despite not being on the allowlist
```

The `beforeSwap` hook in `SwapAllowlistExtension` receives `sender = address(router)`, looks up `allowedSwapper[pool][router]`, finds `true`, and allows the swap. The attacker's address is never consulted. [3](#0-2) [1](#0-0) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
