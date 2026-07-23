### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool forwards, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router, not the end user. If the pool admin allowlists the router (the natural step to let their approved users trade via the router), every unpermissioned user can bypass the per-user allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the first argument the pool passes, which is `msg.sender` of `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is called, the router calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

`msg.sender` inside the pool is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants their allowlisted users to be able to trade via the router must add the router to the allowlist: `allowedSwapper[pool][router] = true`. Once that entry exists, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle()` and the extension check passes, because the router is the `sender` the extension sees.

The same identity loss occurs for `exactInput` (all hops use `msg.sender = router`) and `exactOutputSingle`. The `exactOutput` multi-hop callback path also calls `pool.swap()` from the router context, so the `sender` is still the router for every hop.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC-verified addresses, whitelisted market makers) has its access control fully neutralised for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege: they call a public periphery contract. The pool admin's allowlist configuration is rendered meaningless for the router path, which is the primary user-facing entry point. Any swap the attacker executes is settled at oracle prices and debits real LP liquidity, so LP principal is exposed to adversarial trading from actors the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The likelihood is **medium-high**. The router is the standard user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address, which is the exact configuration that opens the bypass. The attacker needs no special setup: they call a public function on a deployed periphery contract. The only prerequisite is that the pool admin has taken the natural step of allowlisting the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and verifies it. This requires the extension to trust that the router correctly reports the user, which requires the router itself to be a trusted, verified contract (verifiable via the factory registry).

2. **Check `sender` against the factory's pool registry and, when `sender` is a known router, require an additional signed or encoded user identity in `extensionData`**: The extension distinguishes direct calls (where `sender` is the real user) from router calls (where `sender` is the router) and applies the appropriate check.

The simplest safe fix is option 1 combined with a factory-registered router allowlist so the extension can trust the encoded user identity only when the `sender` is a factory-approved router.

---

### Proof of Concept

```
Setup:
  pool P with SwapAllowlistExtension E
  allowAllSwappers[P] = false
  allowedSwapper[P][alice] = true          // alice is the only approved trader
  allowedSwapper[P][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: P, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[P][router] == true  ✓  (no revert)
        → swap executes, LP liquidity consumed, bob receives output tokens

Result:
  bob swaps successfully against the pool despite never being allowlisted.
  The allowlist check is satisfied by the router's address, not bob's.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
