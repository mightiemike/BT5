### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass a per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the originating user. When a pool admin configures a swap allowlist and also allowlists the router to enable router-mediated swaps, any unprivileged user can bypass the allowlist entirely by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so `sender` forwarded to the extension is the **router address**, not the originating user. The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | All allowlisted users are blocked from using the router |
| Allowlist the router | **Any** user bypasses the allowlist through the router |

The bypass path is: unprivileged user → `MetricOmmSimpleRouter.exactInputSingle` → `pool.swap` → `SwapAllowlistExtension.beforeSwap` checks router (allowlisted) → swap succeeds.

This is the direct analog of the H-3 bug: an intermediate contract (CREATE3 proxy / router) is silently substituted for the actual principal (Registry / end-user) in the identity check, making the guard ineffective.

---

### Impact Explanation

A pool configured with a swap allowlist (e.g., an institutional or private LP pool) can be accessed by any unprivileged user through the router. The unauthorized user can execute swaps against the pool's oracle-priced liquidity, draining LP assets at prices the pool admin did not intend to expose to that counterparty. All LP principal in the pool is at risk.

---

### Likelihood Explanation

The trigger requires: (1) a pool with `SwapAllowlistExtension` configured and `allowAllSwappers = false`, and (2) the router allowlisted so that legitimate users can swap via the router. Both conditions are the natural production setup for any allowlisted pool that expects users to interact through the periphery router. No privileged escalation or malicious setup is needed beyond a standard user call to `exactInputSingle`.

---

### Recommendation

The pool must forward the original end-user identity to extensions, not its own `msg.sender`. One approach: add an optional `originator` field to the swap parameters that the router populates with `msg.sender` before calling the pool, and have the pool pass that value as `sender` to extension hooks. Alternatively, the extension can read the originator from a trusted transient-storage slot written by the router before the pool call, similar to how the router already uses transient storage for callback context. [5](#0-4) 

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, allowAllSwappers = false
// Admin allowlists Alice (0xAlice) and the router (0xRouter)
// Bob (0xBob) is NOT allowlisted

// Bob calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));

// Inside pool.swap: msg.sender = router (allowlisted) → extension passes
// Bob receives tokens from a pool he was never authorized to trade against
```

The `beforeSwap` check evaluates `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][bob]` (false), so the revert never fires. [6](#0-5)

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
