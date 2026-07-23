### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If a pool admin allowlists the router (a natural action to support router-mediated swaps for their curated users), every unprivileged user can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` with `msg.sender = router`: [4](#0-3) 

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is never checked.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, every user — including those the admin explicitly excluded — can call `router.exactInputSingle` and the extension passes unconditionally. There is no way to simultaneously (a) allow router-mediated swaps for allowlisted users and (b) block non-allowlisted users, because the extension cannot recover the original user from the router call.

The analog to the bBRO bug is exact: the condition check (allowlist lookup) evaluates an incomplete identity (the router) and silently passes for the wrong actor, just as the staking contract evaluated an incomplete state (zero BRO reward) and silently removed a record that still held bBRO value.

---

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against pool liquidity that the admin intended to reserve for vetted participants. This constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool-admin-configured access control, allowing unauthorized swap execution and potential extraction of LP assets at oracle-quoted prices.

---

### Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the canonical periphery swap path. A pool admin who deploys a curated pool and wants their allowlisted users to benefit from the router's slippage protection, multi-hop routing, and deadline enforcement will naturally add the router to the allowlist. The documentation and pool configuration guide present extensions and the router as complementary features with no warning that allowlisting the router collapses the per-user gate. Any attacker who observes `allowedSwapper[pool][router] == true` on-chain can immediately exploit the bypass.

---

### Recommendation

The extension must check the economically relevant actor — the user who initiated the transaction — not the intermediate contract. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `tx.origin` as a fallback identity**: Only viable if the pool is never called from another contract; generally fragile.

3. **Redesign the extension interface**: Add a dedicated `swapInitiator` field to the `beforeSwap` callback so the pool can pass both the direct caller and the original initiator (e.g., via a transient-storage context set by the router before calling the pool).

The cleanest fix is option 1: the router stores `msg.sender` in `extensionData` and the extension decodes it, with the pool's `onlyPool` guard ensuring only a legitimate pool can invoke the extension.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — router is added so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes against the curated pool's liquidity despite never being allowlisted.

Step 3 is the realistic trigger: without it, Alice cannot use the router either, so the admin is forced to choose between a broken UX for allowlisted users and a fully open pool.

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
