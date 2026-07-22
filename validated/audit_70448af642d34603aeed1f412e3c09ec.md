### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing allowlist bypass via router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to gate swaps by swapper identity, per pool. However, `beforeSwap` checks the `sender` parameter, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks the router's address rather than the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on an allowlisted pool), every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()`, making the router the `msg.sender` to the pool. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-mediated swaps on an allowlisted pool must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether the actual user is on the allowlist. The per-user gate is silently voided for all router-mediated paths.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position recipient), which is correctly preserved through the liquidity-adder path. [4](#0-3) 

---

### Impact Explanation

Pools that deploy `SwapAllowlistExtension` to restrict counterparties (e.g., to prevent toxic arbitrage flow, enforce KYC, or limit to institutional LPs) lose that protection entirely for any user who routes through the router. LP providers in such pools suffer adverse selection losses from the very flow the allowlist was configured to exclude. The loss is direct and ongoing: every swap by a non-allowlisted user that should have been blocked instead executes at the oracle-derived bid/ask, draining LP value through toxic flow.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted on the pool. This is a natural and expected configuration: a pool admin who deploys an allowlisted pool and also wants to support the protocol's standard router must allowlist the router. The admin has no alternative — not allowlisting the router means zero router-mediated swaps work. The bypass is therefore reachable on any allowlisted pool that supports the standard periphery router, which is the common production configuration.

---

### Recommendation

The extension must check the economically relevant actor — the user — not the intermediary. Two approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `callbackData` or `extensionData`, and have `SwapAllowlistExtension` decode and check that address. This requires a coordinated change across the router and extension.

2. **Check `sender` only when called directly; require the router to forward user identity**: Add a convention where the router encodes the real user in `extensionData`, and the extension reads it when `sender` is a known router. This is fragile and not recommended.

The cleanest fix is approach 1: the router should pass the originating user's address as part of `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and gate on that address when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for router-mediated swaps
  - bob is NOT allowlisted

Attack:
  1. bob calls pool.swap() directly
     → beforeSwap receives sender=bob
     → allowedSwapper[pool][bob] == false → revert NotAllowedToSwap ✓

  2. bob calls MetricOmmSimpleRouter.exactInput(...)
     → router calls pool.swap()
     → msg.sender to pool = router
     → beforeSwap receives sender=router
     → allowedSwapper[pool][router] == true → swap succeeds ✗

Result: bob executes a swap in a pool he is explicitly excluded from,
        bypassing the allowlist entirely via the router.
``` [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
