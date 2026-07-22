### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Allowlist Bypass Through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for curated pools), every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

**Pool → Extension call chain**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

At the pool level `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The bypass**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is on the per-user allowlist. A non-allowlisted user calls `router.exactInputSingle(...)` → router calls `pool.swap(...)` → extension sees `sender = router` → allowlist passes → swap executes.

The same path applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user can execute arbitrary swaps, draining LP value at oracle-derived prices or front-running allowlisted participants. This is a complete, fund-impacting bypass of the pool's access-control boundary.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. A pool admin who wants allowlisted users to access the pool through the standard periphery path will naturally allowlist the router — there is no other way to let those users use the router. The design therefore creates a forced choice: either allowlisted users cannot use the router at all, or the allowlist is rendered ineffective for all router callers. The second outcome is the likely production configuration, making the bypass reachable in any real deployment of this extension.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct value, which can be enforced by checking `msg.sender` (the pool) and then verifying the factory-registered router identity.

2. **Check `sender` only when it is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the originating user from a standardised field in `extensionData` and checks that address instead.

Either approach ensures the allowlist gates the user who controls the economic outcome of the swap.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin allowlists alice:   allowedSwapper[pool][alice]  = true
3. Pool admin allowlists router:  allowedSwapper[pool][router] = true
   (necessary so alice can use the router)

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})

5. Router calls pool.swap(bob, ...) → msg.sender at pool = router.

6. Pool calls _beforeSwap(router, bob, ...).

7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← check passes

8. Swap executes; bob receives output tokens.
   The per-user allowlist is completely bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
