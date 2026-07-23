### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the curated allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`. Inside the pool, `msg.sender` is the router address, so `sender` delivered to the extension is the router, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

Two broken outcomes follow:

1. **Allowlist bypass**: The pool admin must allowlist the router address to permit any router-mediated swap. Because the router is a public, permissionless contract, allowlisting it grants every user on the network the ability to swap, completely defeating the curated-pool policy.
2. **Broken functionality**: If the admin allowlists individual user addresses instead, those users cannot swap through the router (the router is not in the allowlist), so the only usable path is a direct pool call — which requires the user to implement `IMetricOmmSwapCallback` themselves.

The analog to the seed bug is exact: just as `reclaimTokens` used `owner` (trustee owner) when it should have used `tokenContract.owner`, `SwapAllowlistExtension` checks `sender` (the router) when it should check the end user.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely the moment the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity at oracle-derived prices, extracting value from LP positions that were intended to be protected. This is a direct loss of LP principal and a broken core pool invariant (curated access control).

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery entry point for swaps. Pool admins who want their allowlisted users to benefit from multi-hop routing or slippage protection must allowlist the router. The bypass is therefore reachable on any curated pool that supports router usage, which is the expected production configuration.

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate contract. Two options:

1. **Pass the original user in `extensionData`**: The router encodes the originating user address in `extensionData`; the extension decodes and verifies it. This requires a trusted router identity check inside the extension.
2. **Check `sender` only for direct calls; require the router to forward user identity**: Add a convention where the router always includes the real swapper in `extensionData`, and the extension falls back to `sender` only when `extensionData` is empty (direct pool call).

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at pool configuration time, or redesign the extension to accept a verified user identity from trusted routers.

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, address(router), true)
   — necessary so that router-mediated swaps are not blocked.
3. Non-allowlisted user Alice calls MetricOmmSimpleRouter.exactInput(...)
   targeting the curated pool.
4. Router calls pool.swap(...); msg.sender inside pool = router.
5. _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → passes.
6. Alice's swap executes against the curated pool's liquidity.
7. The allowlist policy is completely bypassed.
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
