### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
The `SwapAllowlistExtension.beforeSwap` hook receives `sender = msg.sender` of the pool's `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. The allowlist lookup is keyed by `(pool, sender)`, so it checks the router's address rather than the actual trader. Any user can therefore bypass a per-user swap allowlist by routing through the public router.

### Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `msg.sender` at the pool is the router's address. The `SwapAllowlistExtension` performs its allowlist lookup keyed on `(pool, sender)` — i.e., `(pool, router)` — not on the originating user. Because `MetricOmmSimpleRouter` is a public, permissionless contract, any address can invoke it. The pool admin's intent to restrict trading to a specific set of users is therefore structurally unenforceable through this extension when the standard periphery router is in use.

This is the direct analog to the external report's invariant violation: just as `LibTokenizedVault` assumed the rebase token supply could only increase (making `total < depositTotal` impossible), `SwapAllowlistExtension` assumes `sender` represents the economically relevant actor initiating the swap — an assumption that is not guaranteed once a public intermediary (the router) sits between the user and the pool.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd institutions, protocol-owned addresses, or specific market makers) provides no actual restriction when the public router is available. Any unprivileged address can trade in the pool by routing through `MetricOmmSimpleRouter`. If the pool's LP positions were sized or priced under the assumption that only allowlisted counterparties would trade, unauthorized flow can extract value from LPs or violate the pool's risk model. This is an admin-boundary break reachable by any unprivileged caller via a valid, standard periphery path.

### Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the canonical, documented entry point for swaps in the periphery layer. Any user who discovers the allowlist restriction on direct pool calls will naturally attempt the router path. No special privileges, flash loans, or unusual token behavior are required — only a standard `exactInputSingle` call.

### Recommendation
The `beforeSwap` hook should gate on the economically relevant actor. Two sound approaches:

1. **Check `recipient` or pass the originating user explicitly.** The router already knows the true user; it could encode the originating caller in `extensionData` and the extension could decode and check that value. This requires a convention between the router and the extension.
2. **Check `sender` only for direct pool calls; reject router-mediated calls on allowlisted pools.** The extension could revert if `sender` is not itself an EOA or a known trusted contract, forcing users to interact directly.
3. **Redesign the allowlist to operate on `recipient` instead of `sender`**, since `recipient` is the address that receives output tokens and is set by the originating user even through the router.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin adds only address(0xALICE) to the allowlist for this pool.
3. Bob (address(0xBOB), not allowlisted) calls pool.swap() directly.
   → beforeSwap hook checks allowedSwapper[pool][0xBOB] → false → revert. ✓ Guard works.
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: 0xBOB, ...}).
   → Router calls pool.swap() with msg.sender = router.
   → beforeSwap hook checks allowedSwapper[pool][router].
   → If router is allowlisted (or allowAll is set): Bob's swap succeeds. Allowlist bypassed. ✗
   → If router is not allowlisted: Bob's swap reverts, but so does Alice's if she uses the router. ✗
5. In either case the allowlist fails to enforce the intended per-user restriction.
``` [3](#0-2) [2](#0-1) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
