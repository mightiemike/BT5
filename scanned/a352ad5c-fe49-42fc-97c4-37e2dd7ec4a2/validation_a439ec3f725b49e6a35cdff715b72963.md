### Title
Swap Allowlist Bypassed via Router: `msg.sender` Identity Substitution Lets Any EOA Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool sets `sender = msg.sender = router address`. If the pool admin allowlists the router (the natural step to enable router-based swaps for legitimate users), the allowlist check degenerates to "is the router allowlisted?" — which is always true — and any EOA can bypass the per-user restriction by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At the pool level `msg.sender` is the **router contract**, not the originating EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`. If the pool admin has allowlisted the router address — the necessary step to let any legitimate user swap through the router — the check passes for every caller regardless of their individual allowlist status.

The same substitution occurs in `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool) is fully open to any EOA once the router is allowlisted. Non-allowlisted users can execute arbitrary swaps, draining LP positions at oracle-derived prices. Because the pool's liquidity is priced by an external oracle, a non-allowlisted user can execute swaps that the LP never consented to, resulting in direct loss of LP principal.

### Likelihood Explanation

The pool admin faces an unavoidable dilemma: either allowlist the router (breaking the per-user allowlist for all users) or do not (forcing every allowlisted user to call the pool directly, bypassing the periphery entirely). Any deployment that allowlists the router — the expected production configuration — is immediately exploitable by any EOA. The router is a public, permissionless contract, so no special access is required.

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **originating EOA**, not the intermediate caller. Two options:

1. **Pass the real initiator through extensionData**: the router encodes `msg.sender` (the EOA) into `extensionData`; the extension decodes and checks it. This requires a coordinated change in the router and extension.
2. **Check `sender` AND require `sender != router`**: reject router-mediated swaps unless the pool explicitly opts in to router-open mode, keeping per-EOA checks for direct calls.

The cleaner fix is option 1: the router should encode the originating EOA in `extensionData` and the extension should decode and verify it, so the allowlist always gates the economically relevant actor.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intended to let allowlisted users swap via the router.
3. Attacker (not in allowlist) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: restrictedPool,
       recipient: attacker,
       ...
     })
4. Router calls pool.swap(attacker, ...) — msg.sender at pool = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Swap executes; attacker receives output tokens.
   LP suffers unintended trade at oracle price.
```

The attacker needs no special role, no privileged setup, and no non-standard token behavior. The only precondition is that the pool admin has allowlisted the router — the standard operational configuration for any pool that intends to support periphery-routed swaps.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
