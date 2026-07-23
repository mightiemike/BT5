### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted on a curated pool (the only way to enable router-mediated swaps for any user), every unprivileged address can bypass the allowlist by calling through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

So the extension receives `sender = router address`, not the end user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**Two concrete failure modes arise:**

**Mode A — Bypass (fund-impacting):** A pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for every caller who routes through the router, regardless of whether that caller is individually allowlisted. Any unprivileged address can now trade on the curated pool.

**Mode B — DoS (broken core functionality):** If the pool admin does not allowlist the router, individually allowlisted users cannot use `MetricOmmSimpleRouter` at all — their swaps revert with `NotAllowedToSwap` because the extension sees the router, not them. The standard periphery swap path is broken for legitimate users.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner argument), which the liquidity adder passes through correctly regardless of who the `sender` is. [5](#0-4) 

### Impact Explanation

**Mode A** is the higher-severity path. A curated pool with `SwapAllowlistExtension` is designed to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers). Once the router is allowlisted to support the standard periphery flow, the curation is entirely defeated: any address can drain LP liquidity through the pool at oracle-quoted prices, causing direct loss of LP principal. This matches the "allowlist bypass" and "broken core pool functionality causing loss of funds" impact categories.

**Mode B** causes the standard swap interface to be unusable for legitimate users on allowlisted pools, which is broken core functionality.

### Likelihood Explanation

The trigger is unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The precondition for Mode A is that the pool admin has allowlisted the router — a natural operational step for any pool that intends to support the standard periphery. The precondition for Mode B requires only that the pool has `SwapAllowlistExtension` active and the router is not individually allowlisted, which is the default state. Both modes are reachable through normal, documented usage of the protocol's own periphery contracts.

### Recommendation

The extension should check the original end-user identity, not the immediate pool caller. Two options:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a protocol-level convention for the extension payload.

2. **Check `sender` against the allowlist but also accept the router as a transparent forwarder**: Require the router to be a known, factory-registered contract and check the original user from a router-specific callback or transient context. This is architecturally cleaner.

The simplest safe fix is to have the router forward the original caller's address in a standardized prefix of `extensionData`, and have `SwapAllowlistExtension` decode and gate on that address when the immediate `sender` is a known router.

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — necessary to allow any router-mediated swap.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack (Mode A — bypass):
  4. attacker calls router.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...) with msg.sender = router.
  6. Pool calls _beforeSwap(router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  8. Swap executes. attacker receives output tokens.
  9. Allowlist invariant broken: attacker was never individually allowlisted.

Attack (Mode B — DoS):
  4. Pool admin calls setAllowedToSwap(pool, alice, true).
  5. alice calls router.exactInputSingle({pool: pool, ...}).
  6. Router calls pool.swap(...) with msg.sender = router.
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → false.
  8. Revert: NotAllowedToSwap.
  9. alice cannot use the standard periphery despite being individually allowlisted.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
