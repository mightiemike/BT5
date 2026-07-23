### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way to let users trade through it), the allowlist is bypassed for every user on-chain.

---

### Finding Description

**Pool passes its own `msg.sender` as `sender` to every extension hook.**

In `MetricOmmPool.swap`, the `_beforeSwap` dispatcher is called with `msg.sender` as the first argument: [1](#0-0) 

That value propagates unchanged through `ExtensionCalling._beforeSwap` into the ABI-encoded call to every configured extension: [2](#0-1) 

**`SwapAllowlistExtension` checks that `sender` argument as the swapper identity.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

**`MetricOmmSimpleRouter` is the pool's `msg.sender` for every router-mediated swap.**

`exactInputSingle`, `exactInput`, and `exactOutput` all call `pool.swap(...)` directly from the router contract: [4](#0-3) [5](#0-4) 

The actual end user is `msg.sender` of the router, which is never forwarded to the pool or the extension.

**Result — two broken invariants:**

| Pool admin intent | What the extension checks | Outcome |
|---|---|---|
| Allowlist individual users; do NOT allowlist the router | `allowedSwapper[pool][router]` → false | Allowlisted users **cannot** swap through the router; core swap flow is broken |
| Allowlist the router so users can trade | `allowedSwapper[pool][router]` → true | **Every** user on-chain can bypass the allowlist via the router |

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner explicitly passed by the caller), not `sender`: [6](#0-5) 

The swap extension has no equivalent "real user" field to check, so the mismatch is structural.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties) is fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). The pool receives and settles the swap normally; the allowlist guard never sees the real user. This constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) defeats a pool-admin-configured access control, allowing unauthorized users to trade against pool liquidity.

---

### Likelihood Explanation

The router is the canonical, documented entry point for swaps in the Metric OMM periphery. Any user can call it without special permissions. The bypass requires no flash loans, no price manipulation, and no privileged access — only a standard `exactInputSingle` call. Every curated pool that uses `SwapAllowlistExtension` and permits router-mediated swaps is affected.

---

### Recommendation

The extension must gate the **economic actor** — the address that controls the input tokens and initiates the trade — not the intermediate contract. Two viable approaches:

1. **Require the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct value, which is acceptable given it is a protocol-controlled contract.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user, but this breaks for multi-hop flows where intermediate recipients are the router itself.

3. **Allowlist at the router level**: Add a separate allowlist enforced by the router before it calls the pool, and document that `SwapAllowlistExtension` only gates direct pool callers.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding, and the extension decodes and checks that address.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool: only `allowedUser` may swap.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attacker (not allowlisted) calls the router directly.
// The router calls pool.swap(...) with msg.sender = router.
// The extension checks allowedSwapper[pool][router] → false by default,
// BUT if the admin also allowlisted the router to let allowedUser trade,
// the check passes for the attacker too.

router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        recipient:       attacker,
        zeroForOne:      false,
        amountIn:        1000,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
// Swap succeeds; attacker receives token0 from a pool they are not allowlisted on.
```

The root cause is that `pool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`, and `SwapAllowlistExtension.beforeSwap` checks that router address against the allowlist instead of the actual end user. [3](#0-2) [1](#0-0) [7](#0-6)

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
