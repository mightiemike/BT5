### Title
`SwapAllowlistExtension` gates on the router address instead of the original user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` inside `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the original user. If the router is allowlisted (which is required for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool: [2](#0-1) 

When a user calls the pool directly, `sender = user` — the check is correct. When a user calls through `MetricOmmSimpleRouter`, `sender = router` — the check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether the original user is on the allowlist.

The analog to the external report is exact: just as `verifyState` inspects one value with a type cast and misses the real layout change, `SwapAllowlistExtension` inspects one identity (the router) and misses the real actor (the original user). The guard appears to function but checks the wrong thing.

### Impact Explanation

Any user can swap on a curated pool that is intended to be restricted to specific participants (e.g., KYC'd users, institutional counterparties, whitelisted protocols). The allowlist protection is completely nullified for the router path. This is a direct admin-boundary break: the pool admin's curation policy is bypassed by an unprivileged actor using a supported public periphery contract.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys a curated pool and wants allowlisted users to be able to use the router must allowlist the router. This is the expected operational setup, making the bypass reachable in every realistic curated-pool deployment. No special privileges or malicious setup are required — a standard router call suffices.

### Recommendation

The extension must gate on the **original user**, not the immediate pool caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Check `sender` against a router registry and then verify the original user**: The extension recognizes known routers and requires the original user to be passed explicitly and verifiably (e.g., via a signed payload or a dedicated router interface that exposes the originating caller).

The simplest safe fix is to not allowlist the router at all and require allowlisted users to call the pool directly — but this breaks router usability. The correct long-term fix is to thread the original caller identity through the extension data path so the allowlist can always gate the economically relevant actor.

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
   → router calls pool.swap(recipient, ...)
   → pool passes msg.sender = router as `sender` to _beforeSwap
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
   → Bob's swap executes on the curated pool despite not being allowlisted
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
