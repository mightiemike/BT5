### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. A pool admin who allowlists the router (required for any router-mediated swap to succeed) simultaneously opens the gate to every user on the network.

### Finding Description

**Root cause — wrong actor bound in the hook:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

`msg.sender` inside `pool.swap()` is the **router**, not the original user. The extension therefore evaluates `allowedSwapper[pool][router]`.

**The two broken outcomes:**

| Pool admin decision | Result |
|---|---|
| Does **not** allowlist the router | Every router-mediated swap reverts, even for allowlisted users — broken core functionality |
| **Allowlists the router** | Every user on the network can bypass the per-user allowlist by calling through the router |

The router is a public, permissionless contract. Any user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. There is no mechanism in the router to embed the original user's identity into the `sender` argument seen by the extension; `extensionData` is user-controlled and trivially forgeable.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool intended to restrict trading to a specific set of addresses. Once the router is allowlisted (the only way to enable router-mediated swaps for legitimate users), the allowlist is completely ineffective: any address can trade by routing through `MetricOmmSimpleRouter`. This is a direct, unprivileged bypass of the pool's access-control policy with no additional preconditions beyond knowing the router address.

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any user who reads the protocol documentation or inspects on-chain transactions will discover it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only a single public function call to the router.

### Recommendation

The `sender` argument forwarded to extensions must represent the **original user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side:** Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` in a tamper-evident way (e.g., a dedicated ABI-encoded prefix that the extension can decode and verify came from a trusted router).
2. **Extension-side:** `SwapAllowlistExtension` should accept a trusted-router registry and, when `sender` is a known router, extract and verify the real user from `extensionData` before performing the allowlist lookup.

Alternatively, document clearly that `SwapAllowlistExtension` only gates direct pool calls and that router-mediated swaps are always unrestricted, and rename/redesign accordingly.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // required for any router swap
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  bob is NOT in the allowlist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  pool.swap() is called with msg.sender = router
  _beforeSwap(router, ...) is dispatched
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  bob's swap executes successfully despite not being allowlisted
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
