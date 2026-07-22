The call chain is fully traceable. Here is the analysis:

**Call chain:**
1. User → `MetricOmmSimpleRouter::exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`)
2. Router → `MetricOmmPool::swap(recipient, ...)` — here `msg.sender` inside the pool = **router address**
3. Pool → `ExtensionCalling::_beforeSwap(msg.sender, ...)` — passes router address as `sender`
4. Extension → `SwapAllowlistExtension::beforeSwap(sender=router, ...)` — checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool, `sender` = router

The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

**The `sender` identity is the router, not the original EOA.** This is confirmed by:

- `MetricOmmPool::swap` passes `msg.sender` as the first argument to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling::_beforeSwap` forwards that `sender` directly to the extension [2](#0-1) 
- `SwapAllowlistExtension::beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called the pool (the router) [3](#0-2) 
- The router calls the pool directly with no mechanism to forward the original user's address as `sender` [4](#0-3) 

---

### Title
Swap allowlist checks router address instead of original user — any user can bypass per-user allowlist via router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the original user's address. If the router is allowlisted for a pool, every user on the network can bypass the per-user allowlist by routing through it.

### Finding Description
`MetricOmmPool::swap` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` (the direct caller) as `sender`. When the router is the direct caller, `sender = address(router)`.

`SwapAllowlistExtension::beforeSwap` then evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
```
`msg.sender` here is the pool; `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

**Consequence A — allowlist bypass:** If the pool admin allowlists the router (so that allowlisted users can use it), every unprivileged user can also swap through the router. The per-user allowlist is completely bypassed.

**Consequence B — allowlisted users locked out of router:** If the pool admin allowlists only specific EOAs (not the router), those users cannot use the router at all, even though they are allowlisted. This breaks the expected UX and forces direct pool interaction.

Neither consequence is recoverable without redesigning the extension, because the pool has no mechanism to propagate the original `tx.origin` or a user-supplied identity in a trustless way.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled bots) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. This allows unauthorized parties to execute swaps in pools that were designed to be access-controlled, potentially draining liquidity at prices the pool admin intended only for trusted counterparties.

### Likelihood Explanation
The router is a standard, publicly deployed periphery contract. Any user who discovers that the router is allowlisted for a restricted pool can immediately exploit this. No special privileges, flash loans, or multi-block timing are required. The likelihood is high whenever a pool uses `SwapAllowlistExtension` with the router allowlisted.

### Recommendation
The extension must receive the original user's identity, not the intermediate caller's. Options:
1. Pass `tx.origin` as an additional parameter from the pool to the extension (introduces `tx.origin` trust assumptions).
2. Require the router to pass the original user's address in `extensionData`, and have the extension decode and verify it — but this requires the extension to trust the router, which must itself be verified.
3. The cleanest fix: the pool should pass both `msg.sender` (the direct caller) and an optional `originSender` field that the router populates, with the extension checking the appropriate field based on whether the direct caller is a trusted router.

### Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true  (admin enables router for allowlisted users)
  allowedSwapper[pool][alice] = true
  allowedSwapper[pool][bob] = false    (bob is NOT allowlisted)

Attack:
  bob calls MetricOmmSimpleRouter::exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] → true
  → swap proceeds for bob, who was never allowlisted

Result:
  Bob executes a swap in a pool that was designed to exclude him.
  The allowlist invariant is broken.
``` [3](#0-2) [1](#0-0) [4](#0-3) 

---

**Note on the question's framing:** The claims about "velocity-envelope bypass," "per-block price-change cap," "stale or mis-squared values," and "bin-local liquidity positions at a guard threshold" do not correspond to any logic in `SwapAllowlistExtension`. That extension contains no velocity guard, no price-change cap, and no bin-position threshold. The real and only finding here is the swapper identity mismatch described above.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
