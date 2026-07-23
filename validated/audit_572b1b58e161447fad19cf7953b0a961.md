### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user. If the router is allowlisted (required for any router-mediated swap to succeed), every user — including non-allowlisted ones — can bypass the curated pool's swap restriction by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter` intermediates the swap, it calls `pool.swap()` itself, so `sender` = router address. The allowlist check therefore evaluates the router's address, not the actual economic actor. The pool admin faces an impossible choice:

- **Router NOT allowlisted**: allowlisted users cannot use the router at all (broken core functionality).
- **Router IS allowlisted**: every user — including non-allowlisted ones — can bypass the restriction by routing through the router.

This is structurally identical to the external `authority_bump` bug: a parameter used in one context (`sender` = router in the extension check) does not match the intended identity (`sender` = actual user as the pool admin configured), causing the guard to be misapplied.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or institutional users is fully bypassed. Any unprivileged user can trade on the curated pool by calling the router instead of the pool directly. This breaks the core allowlist invariant and allows unauthorized value extraction from a pool whose liquidity providers deposited under the assumption that only vetted counterparties could trade against them.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user aware that a pool uses `SwapAllowlistExtension` can trivially bypass it by using the router. No privileged access, special tokens, or unusual conditions are required.

### Recommendation

Pass the original economic actor through the call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Extension-data approach**: require the router to encode the originating user in `extensionData`, and have `SwapAllowlistExtension` decode and check that address (with the pool verifying the router is trusted).
2. **Sender-override approach**: add an authenticated `swapOnBehalf(address realSender, ...)` entry point on the pool that trusted periphery contracts can call, passing the real user as `sender` to extensions.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router to work
4. Bob (not KYC'd) calls router.exactInput({pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls extension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true  → PASSES
5. Bob successfully swaps on the curated pool, bypassing the allowlist.
``` [4](#0-3) [5](#0-4)

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
