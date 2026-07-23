### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the internet can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router stores the actual user only in its own transient payment context (`_getPayer()`), never forwarding it to the pool or the extension. The extension therefore sees `sender = router` for every router-mediated swap.

A pool admin who wants to allow router-mediated swaps for allowlisted users has only one option: allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check at line 37 of `SwapAllowlistExtension` passes for **any** caller of the router, regardless of whether that caller is on the allowlist.

The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., trusted market makers, KYC-verified counterparties, or protocol-controlled addresses). Once the router is allowlisted — a necessary step for the pool to be usable through the standard periphery — the allowlist is completely ineffective. Any unprivileged user can call `exactInputSingle` on the router and trade on the pool at oracle prices. This breaks the core access-control invariant of the extension, exposes LP funds to adverse selection from untrusted counterparties, and constitutes a direct loss of LP principal if the pool was designed to trade only with trusted actors.

### Likelihood Explanation

The bypass requires only a standard call to the public `MetricOmmSimpleRouter`. No special privileges, flash loans, or multi-step setup are needed. Any user who discovers the allowlist can trivially route around it. The likelihood is high whenever a curated pool is deployed with `SwapAllowlistExtension` and the router is allowlisted.

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor**, not the intermediary. Two sound approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is the economic beneficiary. Gating `recipient` prevents unauthorized users from receiving output tokens, regardless of which intermediary calls the pool.

Either way, the extension must not treat a shared public intermediary (the router) as a proxy for individual user identity.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  pool admin does NOT allowlist attacker

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      <curated pool>,
      recipient: attacker,
      ...
  })

  pool.swap(msg.sender=router) ->
    _beforeSwap(sender=router, ...) ->
      SwapAllowlistExtension.beforeSwap(sender=router):
        allowedSwapper[pool][router] == true  -> PASSES
  
  Swap executes. Attacker receives output tokens.
  Allowlist is completely bypassed.
``` [6](#0-5) [1](#0-0) [4](#0-3)

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
