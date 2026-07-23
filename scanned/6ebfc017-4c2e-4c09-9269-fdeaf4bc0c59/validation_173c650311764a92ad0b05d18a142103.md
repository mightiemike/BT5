### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` at the time `pool.swap()` is called. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the originating user. Because the router is a public, permissionless contract, any user who is not on the allowlist can bypass the gate by routing through the router — provided the router address itself is allowlisted (which is required for any legitimate user to swap via the router at all).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router is a public, permissionless contract — it imposes no access control on callers. Therefore:

- If the pool admin **does not** allowlist the router, legitimate users cannot swap through the router (the pool requires `msg.sender` to implement `IMetricOmmSwapCallback`, so direct EOA calls are impossible without a callback contract).
- If the pool admin **does** allowlist the router (the only practical option for legitimate users), the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including those not individually allowlisted.

The allowlist is therefore structurally ineffective for any pool that relies on the public router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled accounts) provides no actual restriction when the public router is in use. Any unpermissioned address can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This breaks the core access-control invariant the extension is designed to enforce and constitutes broken core pool functionality.

---

### Likelihood Explanation

The trigger requires no special privilege. Any EOA can call the public router. The condition that makes the bypass reachable — the router being allowlisted — is the only configuration under which legitimate users can swap at all, making it the expected production configuration for any pool that uses both the allowlist extension and the router.

---

### Recommendation

The `sender` value forwarded to extensions should reflect the **originating user**, not the intermediate router. Two complementary fixes:

1. **Router-side:** Have `MetricOmmSimpleRouter` pass the original `msg.sender` as `callbackData` and expose it via a transient-storage accessor so extensions can read the true initiator. Alternatively, add a dedicated transient slot for the "original sender" that the pool reads and forwards to extensions instead of its own `msg.sender`.

2. **Extension-side:** `SwapAllowlistExtension` should check the `recipient` or a separately attested originator rather than `sender` when the sender is a known router. A cleaner solution is to have the pool always forward the transaction origin or a router-attested identity to extension hooks.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)
  - Admin calls setAllowedToSwap(pool, router, true)   // required for alice to use the router

Attack:
  - mallory (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Extension checks allowedSwapper[pool][router] → true
  - Mallory's swap executes on the restricted pool

Result:
  - SwapAllowlistExtension passes for mallory
  - Allowlist is fully bypassed for any caller of the public router
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
