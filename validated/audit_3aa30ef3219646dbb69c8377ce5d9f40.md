### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, letting any user bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist checks the **router's address**, not the actual user's address. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed on the curated pool), every unpermissioned user can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` always passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` = router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For any router-mediated swap to work on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for **every** caller of the router, regardless of whether the actual end-user is individually permitted.

The `DepositAllowlistExtension` has the same structural pattern but gates on `owner` (the LP position owner), which is a caller-supplied parameter — a separate but related concern: [4](#0-3) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market participants, or a private liquidity venue) is fully bypassed. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInput*` targeting the pool and execute swaps as if they were allowlisted. This breaks the core invariant of the allowlist extension and constitutes a direct policy bypass with fund-impacting consequences: unauthorized users can drain liquidity at oracle prices from a pool that was designed to serve only approved counterparties.

---

### Likelihood Explanation

Likelihood is high. The router is the standard, documented periphery entrypoint. For a pool admin to make their allowlisted pool usable at all through the router, they must add the router to the allowlist — at which point the bypass is unconditional. No privileged action by the attacker is required; calling the public router is sufficient. The misconfiguration is not a mistake by the admin but a structural consequence of how `sender` is bound.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor**, not the intermediary. Two options:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` accept a `swapper` parameter and forward it to the pool as an additional field in `extensionData`, then have the extension decode and check that field. This requires a coordinated change across router and extension.

2. **Check `sender` only when it is not a known router, and check `extensionData` for the real user otherwise**: The extension can decode a signed or router-attested user identity from `extensionData` when `sender` is the router.

The simplest safe fix is option 1: the router encodes `msg.sender` (the real user) into `extensionData`, and the extension decodes and checks that address when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router to work
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. Alice (disallowed user) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=alice, ...) → msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes successfully for Alice despite her not being individually allowlisted.

Result:
  Alice bypasses the per-user swap allowlist entirely.
  Any user can repeat this as long as the router remains allowlisted.
``` [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
