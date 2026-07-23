### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end-user. A pool admin who allowlists the router (the only way to permit router-mediated swaps for any allowlisted user) inadvertently opens the gate to every user on the network, completely defeating the per-user access control the extension is designed to enforce.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInput*()
     → MetricOmmPool.swap(recipient, ..., extensionData)   [msg.sender = router]
     → ExtensionCalling._beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. For a direct swap that is the real user; for a router-mediated swap it is the router contract.

**The structural mismatch:** A pool admin who wants allowlisted users to be able to swap through the router must add the router to `allowedSwapper[pool]`. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router — regardless of who the actual end-user is. There is no mechanism in the pool or the extension to recover the original `tx.origin` or a user-supplied identity in a trust-minimised way.

This is the direct analog of the external report: just as `optional_royalty_pct` is consumed without verifying the token standard's enforcement requirement, `sender` is consumed without verifying whether it represents the actual swapper or an intermediary router — and the configured guard is silently misapplied.

---

### Impact Explanation

Any user can bypass a pool's `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`:

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted counterparties.
2. Admin allowlists the router so that whitelisted users can use the standard periphery.
3. Any non-whitelisted user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The extension sees `sender = router`, which is allowlisted, and returns success.
5. The unauthorized swap executes, moving pool funds at oracle-derived prices.

The allowlist is the only on-chain enforcement layer for restricted pools; bypassing it means unrestricted access to pool liquidity, enabling unauthorized price-impact trades against LP capital.

---

### Likelihood Explanation

- **Trigger is unprivileged:** any EOA or contract can call the public router.
- **Router is a standard periphery contract** that pool admins are expected to support; allowlisting it is the natural operational step.
- **No special precondition** beyond the router being allowlisted, which is the common-case configuration for any pool that wants to support the standard swap UI.
- The `SwapAllowlistExtension` mapping and setter are public and verifiable on-chain, so an attacker can confirm the router is allowlisted before executing.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `tx.origin` as a fallback** — if `sender` is a known router/contract, fall back to `tx.origin`. This is acceptable here because the extension is already a trust-gating mechanism and `tx.origin` is the correct identity for EOA-initiated flows.

2. **Require the router to forward the real user identity** — add a `swapper` field to the `extensionData` bytes that the router populates with `msg.sender` before calling the pool. The extension decodes and checks that field when `sender` is a recognised router address.

3. **Do not allowlist the router at the pool level** — instead, require users to call the pool directly. This is the safest option but limits UX.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists the router so that alice (a whitelisted user) can use the UI.
allowlistExt.setAllowedToSwap(pool, address(router), true);
allowlistExt.setAllowedToSwap(pool, alice, true);
// bob is NOT allowlisted.

// bob calls the router directly — extension sees sender=router, passes.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds; bob bypassed the allowlist.
```

The `beforeSwap` hook receives `sender = address(router)`, which is in `allowedSwapper[pool]`, so the guard returns `IMetricOmmExtensions.beforeSwap.selector` and the swap proceeds. [4](#0-3) [5](#0-4) [2](#0-1)

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
