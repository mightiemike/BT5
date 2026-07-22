### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user swapper, allowing any user to bypass the configured swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. The allowlist check is therefore performed against the router, not the actual economic actor, breaking the admin-configured access boundary.

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
User → MetricOmmSimpleRouter.exact*()
     → pool.swap(recipient=user, ...)   ← msg.sender = router
     → Extension.beforeSwap(sender=router, recipient=user, ...)
     → allowedSwapper[pool][router]     ← checks router, not user
```

The extension is documented as gating "swap by swapper address, per pool," but the implementation gates the **router contract address**, not the end user. This produces two failure modes:

1. **Allowlist bypass**: If the pool admin allowlists the router (treating it as a trusted intermediary), every user — including those the admin intended to exclude — can swap freely by routing through it.
2. **Allowlisted users locked out of the router**: If the router is not allowlisted, legitimate allowlisted users cannot use the supported periphery path and must call the pool directly, breaking the intended UX.

The most impactful scenario is (1): a pool configured for a curated set of counterparties (e.g., KYC-gated, institutional-only) is fully open to any user who routes through the public router.

### Impact Explanation

High. A pool admin who configures `SwapAllowlistExtension` to restrict trading to specific addresses has their access boundary deterministically bypassed by any user routing through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP liquidity that was never intended to be available to them, extracting value from LPs at oracle-derived prices. This is an admin-boundary break via an unprivileged public path.

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool that deploys `SwapAllowlistExtension` and expects the router to be a valid entry point will be vulnerable. The bypass is deterministic and requires no special conditions — any user can route through the router.

### Recommendation

The extension must check the **end user**, not the intermediary. Two approaches:

1. Pass the end user's address explicitly through the extension data (`extensionData`) and verify it inside `beforeSwap`. The router would encode `msg.sender` into `extensionData` before forwarding to the pool.
2. Add a `recipient` check instead of (or in addition to) `sender`, since `recipient` is the address that receives output tokens and is more likely to represent the economic actor in router flows.

The cleanest fix is to have the router encode the originating user in `extensionData` and have the extension decode and check that address, with a fallback to `sender` for direct pool calls.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
SwapAllowlistExtension ext = new SwapAllowlistExtension(address(factory));
// pool admin allowlists alice
ext.setAllowedToSwap(address(pool), alice, true);
// pool admin also allowlists the router (thinking it is a trusted intermediary)
ext.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted
// bob routes through MetricOmmSimpleRouter
// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] → true
// bob's swap succeeds despite not being allowlisted
vm.prank(bob);
router.exactInput(...); // succeeds — allowlist bypassed
```

If the admin does **not** allowlist the router, the second failure mode triggers: alice (who is allowlisted) cannot use the router at all, because the extension sees `sender = router` and reverts `NotAllowedToSwap`. [4](#0-3) [5](#0-4) [2](#0-1)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
