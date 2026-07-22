### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` always passes `msg.sender` (the immediate caller) as `sender`, routing through `MetricOmmSimpleRouter` causes the extension to see the **router address** instead of the originating user. Once the router is allowlisted — which is required for any allowlisted user to trade through the supported periphery — every non-allowlisted user can bypass the guard by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` inside the pool, so `sender` delivered to the extension is the **router address**, not the originating user. The extension has no visibility into who initiated the router call.

This creates an inescapable dilemma for pool admins:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade through the supported periphery |
| **Allowlist the router** | Every non-allowlisted user bypasses the guard by routing through the router |

The second branch is the bypass: once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for every user who routes through the router, regardless of whether that user is individually allowlisted. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a **curated pool** — the admin intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). Once the router is allowlisted to support the standard periphery flow, any address can trade on the curated pool by calling `MetricOmmSimpleRouter`. This breaks the core curation invariant and allows unauthorized users to drain or manipulate pool liquidity against the LP's intent. The LP's principal is directly at risk from trades they never consented to allow.

---

### Likelihood Explanation

The trigger is fully unprivileged — any EOA or contract can call `MetricOmmSimpleRouter`. The precondition (router is allowlisted) is the natural operational state: a pool admin who deploys a curated pool and wants allowlisted users to use the standard router **must** allowlist the router, making the bypass immediately reachable. No special timing, flash loan, or privileged access is required.

---

### Recommendation

The extension must recover the **originating user** rather than the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should accept a `swapper` parameter and forward it as part of `extensionData`; `SwapAllowlistExtension` should decode and check that value instead of `sender`.

2. **Check `sender` against a router registry and fall back to `extensionData`**: If `sender` is a known trusted router, decode the real originator from `extensionData` and apply the allowlist check to that address.

Either way, the invariant must be: the address checked by the allowlist is the address that economically benefits from the swap, not the intermediary contract that relayed it.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists alice (legitimate user) and the router (so alice can use it).
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted.
// bob routes through the public router:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: bob,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// pool.swap() is called with msg.sender = router.
// SwapAllowlistExtension sees sender = router → allowedSwapper[pool][router] = true → passes.
// bob successfully swaps on a pool he was never authorized to access.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
}
```
