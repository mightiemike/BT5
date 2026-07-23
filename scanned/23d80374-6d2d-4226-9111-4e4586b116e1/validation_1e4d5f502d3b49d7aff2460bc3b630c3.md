### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool at the time `swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural step to enable legitimate router usage), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly — making the pool's `msg.sender` the router address, not the end user: [4](#0-3) 

The same applies to every other router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback`): [5](#0-4) [6](#0-5) 

The result is a two-outcome failure:

| Pool admin action | Consequence |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all — broken core swap flow |
| **Does** allowlist the router (to enable legitimate router usage) | `allowedSwapper[pool][router] = true` → every user, allowlisted or not, can call the router and bypass the gate |

The second outcome is the direct bypass. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the gate to every other user.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unpermissioned address can execute swaps against the pool's liquidity, draining LP value at oracle-derived prices without the intended access control. This is a direct loss of LP principal and a complete break of the pool's intended curation invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the periphery. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and then tries to let their allowlisted users trade through the router will naturally allowlist the router address, triggering the bypass. The attacker requires no special privilege — a single public call to `exactInputSingle` suffices.

---

### Recommendation

The extension must gate on the actual economic actor, not the immediate pool caller. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Dedicated router forwarding field**: Add an explicit `originator` parameter to the swap interface (or a standardized prefix in `extensionData`) that the pool passes through to extensions, allowing extensions to distinguish the end user from the routing contract.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (explicitly passed by the caller) rather than `sender`, which is the correct pattern for the deposit path. [7](#0-6) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = 1.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack:
  4. bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
     → router calls pool.swap(recipient, ...)
     → pool passes msg.sender = router as `sender` to _beforeSwap
     → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
     → hook passes, swap executes
     → bob receives output tokens despite never being allowlisted

Result:
  bob successfully swaps on a pool that was supposed to restrict trading to alice only.
  The allowlist invariant is broken; LP funds are exposed to any public caller.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
