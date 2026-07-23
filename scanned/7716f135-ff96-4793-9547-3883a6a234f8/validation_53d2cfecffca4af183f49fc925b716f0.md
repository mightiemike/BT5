### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper when swaps enter through `MetricOmmSimpleRouter`, enabling complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of its own `swap` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the individual user. If the router is allowlisted (the only way to let users access the pool through the standard periphery), every user on-chain can bypass the per-user restriction.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle
         → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, ...)
              // msg.sender to pool = router address
         → MetricOmmPool._beforeSwap(msg.sender=router, ...)
         → ExtensionCalling._callExtensionsInOrder
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When the router calls the pool, `sender = address(router)`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Two broken outcomes result:**

1. **Broken functionality:** Pool admin allowlists specific user addresses. Those users call `MetricOmmSimpleRouter.exactInputSingle`. The pool sees `msg.sender = router`; the router is not allowlisted; the swap reverts with `NotAllowedToSwap`. Allowlisted users cannot use the standard periphery.

2. **Complete bypass:** To fix (1), the admin allowlists the router address. Now `allowedSwapper[pool][router] = true`. Any user — including those the admin explicitly never allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes, because it only sees the router address.

The router does not forward the original caller's identity to the pool in any way. `_setNextCallbackContext` stores `msg.sender` only for the payment callback, not for the extension `sender` argument.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to create a curated or permissioned trading venue (e.g., KYC-gated, institution-only, or regulatory-restricted) cannot enforce per-user access control when the standard `MetricOmmSimpleRouter` is in use. Any unprivileged user can bypass the allowlist by routing through the public router. This constitutes a complete failure of the access-control guard, allowing unauthorized users to drain LP liquidity or execute trades the pool was designed to prevent. The LP principal is directly at risk if the pool's economic design depends on restricting counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no admin cooperation. The bypass is reachable on every swap through the router on every pool that uses `SwapAllowlistExtension` with the router allowlisted.

---

### Recommendation

Pass the original user's identity through the extension `sender` argument rather than the immediate `msg.sender`. One approach: the router stores the original caller in transient storage before calling the pool, and the pool reads it as the authoritative `sender` for extension dispatch. Alternatively, `SwapAllowlistExtension` should check `recipient` or require the pool to be called directly (no router intermediary) for allowlisted pools. At minimum, the NatSpec on `SwapAllowlistExtension` must warn that allowlisting the router grants access to all router users, and the extension should document that per-user enforcement requires direct pool calls only.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is KYC'd
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender = router, ...)
      → SwapAllowlistExtension.beforeSwap(sender = router, ...)
      → allowedSwapper[pool][router] == true  → passes
      → bob's swap executes against LP funds
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
