### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. For the router to work at all on an allowlisted pool, the admin must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` field of the `IMetricOmmExtensions.beforeSwap` ABI call: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender`, which is the router.**

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

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whatever the pool received as its own `msg.sender`. When the router calls the pool, `sender = address(router)`.

**Step 3 — The router calls the pool directly with no user-identity forwarding.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no mechanism to pass the originating user's address: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Step 4 — The forced dilemma.**

For a pool with `SwapAllowlistExtension` to accept any router-mediated swap, the admin must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, the extension evaluates `allowedSwapper[pool][router] == true` for every user who routes through the router, regardless of whether that user is individually allowlisted or explicitly excluded. There is no path through the router that lets the extension distinguish `userA` (allowlisted) from `userB` (not allowlisted). [6](#0-5) 

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The bypassing user can execute swaps against the pool's LP reserves at oracle-derived prices, draining value from LPs who deposited under the assumption that only approved counterparties could trade. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard fails open on the router path).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Any pool operator who wants to support router-mediated swaps for their allowlisted users must allowlist the router. The moment they do, the guard is universally bypassed. The trigger requires no special privilege — any public user can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the immediate caller of the pool. Two complementary fixes:

1. **Router-level**: Have the router encode the originating user's address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when present, falling back to `sender` for direct pool calls.

2. **Extension-level**: `SwapAllowlistExtension` should reject calls where `sender` is a known router/intermediary unless the extension data carries a signed or factory-verified user identity.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the explicitly passed position owner) rather than `sender` (the immediate caller): [7](#0-6) 

The swap allowlist should adopt the same pattern — gate on a caller-supplied, pool-verified identity rather than on the raw `msg.sender` chain.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][userA] = true   // only userA is supposed to swap
  allowedSwapper[P][router] = true  // admin adds this to enable router swaps for userA

Attack:
  userB (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: P,
        recipient: userB,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  Call chain:
    router.exactInputSingle()
      → pool.swap(recipient=userB, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[P][router] == true  ✓  (no revert)
        → swap executes against LP reserves
        → router.metricOmmSwapCallback() pays pool from userB's tokens

Result:
  userB successfully swaps in a pool that was supposed to block them.
  The allowlist guard is silently bypassed.
  LP funds are exposed to an unauthorized counterparty.
``` [3](#0-2) [1](#0-0) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
