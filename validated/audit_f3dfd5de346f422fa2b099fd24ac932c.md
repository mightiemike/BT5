### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-user gate by calling the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the pool admin has allowlisted the router (to permit router-mediated swaps for legitimate users), the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

The same structural issue is noted in the project's own audit-target description: [5](#0-4) 

### Impact Explanation

Any user can swap in a pool that is intended to be restricted to a specific set of addresses. Depending on the pool's purpose (KYC-gated, whitelist-only LP pools, institutional pools), this allows:

- Unauthorized principals to execute swaps and extract value from LP positions.
- Complete nullification of the access-control invariant the pool admin configured.

Because the router is a public, permissionless contract, no special privilege is required beyond knowing the pool address.

### Likelihood Explanation

The trigger condition is that the pool admin has allowlisted the router address in `SwapAllowlistExtension`. This is the natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users — the admin has no on-chain mechanism to distinguish "router called by an allowlisted user" from "router called by anyone." Once the router is allowlisted, the bypass is trivially reachable by any EOA or contract.

### Recommendation

The extension must receive and check the **economic actor** (the end user), not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the originating user through the extension data**: the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router assumption.
2. **Check `tx.origin` as a fallback** (weaker, but closes the router-bypass path for EOA callers).
3. **Preferred**: redesign the `beforeSwap` interface to carry a separate `originator` field that the pool populates from a trusted periphery context (e.g., transient storage set by the router before calling `swap`), so the extension can gate on the true initiator without relying on `tx.origin`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin enables router-mediated swaps
  allowedSwapper[pool][bob]   = false         // bob is NOT allowed

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)
  → pool calls extension.beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives tokens from the restricted pool
```

Bob successfully swaps despite being explicitly excluded from the allowlist. The guard checked the wrong identity — the router — instead of the intended value — the end user — which is the direct analog of the external report's wrong-balance check.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
