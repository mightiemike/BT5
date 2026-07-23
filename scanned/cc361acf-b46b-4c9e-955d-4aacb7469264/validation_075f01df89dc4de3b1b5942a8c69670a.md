### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. The allowlist therefore gates the wrong actor: it checks whether the router is permitted, not whether the end-user is permitted. Any user can bypass a curated pool's swap allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()` on the user's behalf. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for the pool admin:

- **Option A – Do not allowlist the router**: Router-mediated swaps revert for everyone, including legitimately allowlisted users. Core router functionality is broken for the pool.
- **Option B – Allowlist the router**: Every user on the network can bypass the allowlist by routing through the public router, because the extension sees only the router address and passes it.

The analog to the external `LibEntity` bug is exact: the validation checks one representation of the actor (the direct pool caller / `utilizedCapacity`) but the economically relevant value is a derived one (the actual end-user / `newUtilizedCapacity`) that is never verified.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses loses that protection entirely the moment the router is allowlisted (or `allowAllSwappers` is set). Any unprivileged user can execute swaps against the pool by calling `MetricOmmSimpleRouter`, draining LP value or executing trades the pool was designed to prevent. This is a direct loss of the curation invariant and constitutes broken core pool functionality with fund-impacting consequences for LP principals.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the protocol. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router, at which point the bypass is immediately available to all users. The trigger requires no special privilege — any EOA can call the public router.

### Recommendation

The `beforeSwap` hook should gate the **economically relevant actor**, not the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes the real user in `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` instead of `sender` for router flows, or add a transient-storage originator**: The pool could store `tx.origin` or a transient "real sender" set by the router before calling `pool.swap`, and the extension reads that value.
3. **Preferred — document that the allowlist only works for direct pool calls and add an `onlyPool` guard that also validates the sender is not a known router**: Alternatively, redesign the extension to accept a signed permit from the real user embedded in `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)  ← required for router UX

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=bob, ...) → msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes despite not being on the allowlist

Result:
  - Bob receives token output from a curated pool that was supposed to block him
  - The allowlist invariant is violated; LP funds are exposed to unrestricted trading
``` [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
