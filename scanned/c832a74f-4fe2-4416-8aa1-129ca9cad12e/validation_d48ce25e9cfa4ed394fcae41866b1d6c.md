### Title
`SwapAllowlistExtension` gates on the router address instead of the actual end-user, allowing any unprivileged user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted — not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInput*()
     → pool.swap(recipient, ...)          // msg.sender = router
     → _beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to the extension hook: [1](#0-0) 

`_beforeSwap` then forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. The extension has no visibility into who initiated the router call.

---

### Impact Explanation

**Scenario — allowlist bypass (High):**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties).
2. To allow those users to use the router, the admin must also allowlist the `MetricOmmSimpleRouter` address.
3. Once the router is allowlisted, **any** address — including non-allowlisted users — can call `MetricOmmSimpleRouter.exactInput*(...)` and the extension check passes because `sender = router` is allowlisted.
4. The per-user allowlist is completely nullified; the pool is open to all swappers.

**Scenario — broken core functionality (Medium):**

If the admin allowlists individual user addresses but does NOT allowlist the router, those allowlisted users cannot use the router at all (the router's address fails the check). They must implement `IMetricOmmSwapCallback` themselves to call the pool directly, making the router unusable for any allowlisted pool.

Both outcomes represent a broken invariant: the allowlist either fails to gate the intended actors or renders the primary user-facing swap path unusable.

---

### Likelihood Explanation

The operator pattern (payer ≠ beneficiary) is explicitly documented and tested for `addLiquidity`: [4](#0-3) 

The same separation exists for swaps (caller ≠ recipient), but the `SwapAllowlistExtension` was not updated to account for router intermediation. Any pool that deploys `SwapAllowlistExtension` and also wants router support will hit this immediately. The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the README, so the collision is near-certain in production.

---

### Recommendation

The extension must check the **actual end user**, not the intermediary. Two options:

**Option A — check `recipient` instead of `sender`:**
Gate on the address that receives the output tokens. This is the economic beneficiary of the swap and is always the end user, even through the router.

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**Option B — pass the original caller through `extensionData`:**
The router encodes the end user's address in `extensionData`; the extension decodes and checks it. This requires router cooperation and is more complex but preserves the `sender`/`recipient` distinction.

---

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// Alice calls the router — sender seen by the extension is the router, which IS allowlisted.
vm.prank(alice); // alice is not in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        recipient: alice,
        callbackData: "",
        extensionData: ""
    })
);
// ✓ swap succeeds — alice bypassed the per-user allowlist
```

The `beforeSwap` hook receives `sender = address(router)`, which passes `allowedSwapper[pool][router]`, regardless of who called the router. [3](#0-2) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
