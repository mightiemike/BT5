### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the router is allowlisted (required for router-based swaps to function), every user — including non-allowlisted ones — can bypass the restriction by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
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

So `msg.sender` to the pool is the **router**, and `sender` passed to the extension is the **router address**. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For router-based swaps to work on an allowlisted pool, the pool admin must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` always passes — for **every** user who routes through it, regardless of whether that user is individually allowlisted.

The `extensionData` bytes that could theoretically carry the real user's identity are completely ignored by the extension (the last `bytes calldata` parameter is unnamed and unused).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist becomes a gate on the router contract, not on individual users. Non-allowlisted users can freely swap on a pool the admin intended to restrict, causing unauthorized token outflows from the pool and breaking the LP's expectation of a controlled trading environment.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` at any time. No special privilege, flash loan, or callback manipulation is required. The only precondition is that the pool admin has allowlisted the router — which is the natural and expected action for any pool that wants to support router-based trading.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary router. Two viable approaches:

1. **Extension-data identity forwarding:** Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. The extension must also verify that the encoding came from a trusted router (e.g., via a factory-registered router registry).

2. **Separate router-level allowlist:** Add a second mapping `allowedSwapperViaRouter` that the extension checks when `sender` is a known router, falling back to the real user address extracted from `extensionData`.

The simplest safe fix is to not allowlist the router at all and require users to call the pool directly — but this breaks router usability. The correct fix is to thread the real caller identity through `extensionData` and verify it in the extension.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   (admin enables router support)
  allowedSwapper[pool][alice]  = true   (alice is an approved swapper)
  allowedSwapper[pool][bob]    = false  (bob is NOT approved)

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
  → swap proceeds; bob receives tokens

Result:
  bob, a non-allowlisted address, successfully swaps on a restricted pool.
  The allowlist check is applied to the router address (correct value: router)
  instead of the actual swapper (intended value: bob).
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
