### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any Trader to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks the router's address against the allowlist rather than the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router, so `sender` forwarded to the extension is the router's address — not the original user's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

1. **Router not allowlisted**: Allowlisted users cannot use the router at all. Every router-mediated swap reverts `NotAllowedToSwap`, breaking the primary user-facing swap path for legitimate users.
2. **Router allowlisted**: Every user — including those the allowlist was designed to exclude — can bypass the gate by routing through the public `MetricOmmSimpleRouter`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through the public router. Non-allowlisted users can execute swaps against LP funds on pools that were designed to exclude them. If the pool's pricing or liquidity assumptions depend on the identity of counterparties, LP principal is directly at risk from trades that should have been blocked.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any user who discovers the bypass — or who simply uses the router as the default path — triggers it. No privileged access, no special setup, and no malicious token is required. The trigger is a standard `exactInputSingle` or `exactInput` call through the public router.

### Recommendation

The extension must check the economically relevant actor — the original end-user — rather than the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` in the pool, not the extension**: The pool already knows `msg.sender` at the point of the `swap()` call. An alternative design would have the pool enforce the allowlist directly using the original caller, rather than delegating to an extension that only sees the proxied `sender`.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that pools using it must require direct `pool.swap()` calls only, or to add a router-aware identity-forwarding mechanism.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists address(router) so that allowlisted users can use the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true (admin allowlisted router)
  5. Swap executes; attacker receives output tokens

Result: attacker bypasses the allowlist and trades on a curated pool
        that was designed to exclude them, with direct LP exposure.
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
