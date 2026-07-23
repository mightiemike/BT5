### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the router contract, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every unprivileged user, completely defeating the per-user allowlist.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — so `msg.sender` inside the pool is the router.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap()` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. The extension evaluates `allowedSwapper[pool][router]`.

The extension never sees the original user's address. The check is:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`sender` here is the router, not the end user. If the pool admin allowlists the router (which is necessary for any allowlisted user to swap through the router), the condition `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including those the admin intended to block.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` from the router's address.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses are supposed to be able to trade. Once the pool admin allowlists the router (a necessary step for any allowlisted user to use the standard periphery), the allowlist is fully bypassed for all users. Any address can call `exactInputSingle()` on the router and execute swaps on the restricted pool. The pool admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same — the two goals are mutually exclusive given the current actor binding.

Direct consequences:
- Unauthorized users can trade on pools intended to be restricted (e.g., KYC-gated, institutional-only, or compliance-restricted pools).
- LP positions in restricted pools are exposed to swap flow from actors the pool admin explicitly intended to exclude.
- The admin-configured allowlist boundary is broken by an unprivileged, publicly reachable path.

### Likelihood Explanation

Likelihood is high. The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who discovers the pool is restricted can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single call to `exactInputSingle()` suffices.

### Recommendation

The extension must check the **original user**, not the immediate caller of the pool. Two approaches:

1. **Pass the original user through the router.** The router should forward `msg.sender` (the end user) as an additional field in `extensionData`, and the extension should decode and check that address. This requires a protocol-level convention for how the router encodes the originating user.

2. **Check `recipient` instead of `sender`.** For swap allowlists, the economically relevant actor is the recipient of the output tokens. The extension already receives `recipient` as the second parameter; gating on `recipient` instead of `sender` would correctly identify the beneficiary regardless of routing path. However, this changes the semantics of the allowlist (payer vs. recipient).

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it, falling back to `sender` for direct pool calls.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin allowlists address(router) so that allowlisted users can swap via the router.
  - Pool admin does NOT allowlist attacker (0xDEAD).

Attack:
  - attacker (0xDEAD) calls router.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(recipient, ...) — msg.sender in pool = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes for the attacker despite them not being on the allowlist.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
```

**Relevant code locations:**

`SwapAllowlistExtension.beforeSwap` checks `sender` (the router): [1](#0-0) 

`MetricOmmPool.swap` sets `sender = msg.sender` (the router when called via router): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` from the router's address: [3](#0-2) 

`ExtensionCalling._beforeSwap` encodes and dispatches `sender` to the extension: [4](#0-3)

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
