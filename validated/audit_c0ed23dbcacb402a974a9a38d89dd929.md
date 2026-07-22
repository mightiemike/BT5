### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the actual user. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for allowlisted users), the allowlist is completely bypassed for every user — including those the admin explicitly intended to block.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument: [1](#0-0) 

`sender` is populated by `ExtensionCalling._beforeSwap`, which encodes `msg.sender` of the pool's `swap` call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)`. At that point `msg.sender` inside the pool is the **router contract**, so `sender` forwarded to the extension is the router address — not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether that caller is individually allowlisted or explicitly blocked.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary explicitly named in the `addLiquidity` call), which is the correct economic actor: [3](#0-2) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or regulated participants) loses that protection entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool by routing through `MetricOmmSimpleRouter`, draining LP value at oracle-anchored prices that were only intended to be accessible to vetted counterparties. This is a direct loss of LP principal and a complete failure of the pool's curation invariant.

---

### Likelihood Explanation

The scenario is highly realistic. A pool admin who deploys a curated pool and wants allowlisted users to benefit from multi-hop routing will naturally add the router to the allowlist — the extension's name ("SwapAllowlistExtension") and its `setAllowedToSwap` API give no indication that allowlisting the router opens the pool to all users. The `FullMetricExtensionTest` integration test only exercises direct-caller paths (via `TestCaller`), so the router-bypass path is untested: [4](#0-3) 

---

### Recommendation

Replace the `sender`-based check with a check against the **actual end user**. Two viable approaches:

1. **Pass the real user through the router**: Have `MetricOmmSimpleRouter` accept a `swapper` argument and forward it as part of `extensionData`; update `SwapAllowlistExtension` to decode and check that address instead of `sender`.
2. **Check `tx.origin` as a fallback**: When `sender` is a known router, fall back to `tx.origin`. This is simpler but has known limitations in meta-transaction contexts.

The cleanest fix is approach (1): the pool interface already threads `extensionData` through every hook call, so the router can embed the real user identity there without any core-pool changes.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
//   - SwapAllowlistExtension configured
//   - alice is allowlisted; bob is NOT allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Admin also allowlists the router so alice can use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) routes through the router — bypass succeeds:
vm.prank(bob);
router.exactInput(ExactInputParams({
    tokenIn: token0,
    tokenOut: token1,
    pool: address(pool),
    amountIn: 1e18,
    amountOutMinimum: 0,
    recipient: bob,
    extensionData: ""
}));
// Bob receives token1 from the curated pool despite being explicitly blocked.
```

The `beforeSwap` hook sees `sender = address(router)`, which is allowlisted, so the check passes. Bob's address is never consulted.

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
