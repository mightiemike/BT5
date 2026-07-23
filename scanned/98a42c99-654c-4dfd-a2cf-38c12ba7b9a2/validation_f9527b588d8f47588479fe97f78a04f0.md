### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap()` is the **router contract**, not the user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for allowlisted users), every unprivileged address can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every registered extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = pool's msg.sender
)
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

Because the router is `msg.sender` of the pool's `swap()`, the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Consequence**: The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user bypasses the allowlist via the router |
| No | Allowlisted users cannot use the router at all |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting swap access to specific addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses). When the router is allowlisted — the natural configuration for any pool that wants to support the standard periphery — the allowlist is fully bypassed for all users. Any address can execute swaps on a pool that the admin intended to be restricted. This is a broken core pool access-control invariant with direct fund-flow consequences: unauthorized actors can trade against the pool's liquidity at oracle prices, extracting value the admin did not intend to permit.

---

### Likelihood Explanation

The bypass requires only that the pool admin has allowlisted the router, which is the expected operational configuration for any pool that wants to support the standard periphery. The attacker needs no special role, no privileged access, and no unusual token behavior — a single call to `MetricOmmSimpleRouter.exactInputSingle` is sufficient.

---

### Recommendation

The extension must check the **economic actor**, not the immediate caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers and, when `sender` is a router, falls back to checking an address forwarded in `extensionData`.

3. **Simplest fix**: Remove router support from the allowlist model and require allowlisted users to call `pool.swap()` directly, documenting that the router is incompatible with `SwapAllowlistExtension`.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  E.setAllowedToSwap(P, alice, true)      // alice is allowlisted
  E.setAllowedToSwap(P, router, true)     // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: P, recipient: bob, ...})

  Execution trace:
    router → pool.swap(recipient=bob, ...)
      pool: msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[P][router] == true  ← passes
      swap executes, bob receives tokens

Result:
  bob swaps successfully on a pool the admin intended to restrict to alice only.
  The allowlist invariant is broken.
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
