### Title
SwapAllowlistExtension checks router address instead of actual user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every user — including non-allowlisted ones — can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted, keyed by `msg.sender` (the pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

The pool's `msg.sender` is the router address. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

For allowlisted users to be able to use the router at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** caller of the router passes the check, because the extension only sees the router's address. The same bypass applies to `exactInput` (all hops) and `exactOutput` (all recursive callback hops): [4](#0-3) [5](#0-4) 

The allowlist storage and setter confirm the per-pool, per-swapper design intent — individual users are supposed to be gated, not the router: [6](#0-5) 

---

### Impact Explanation

A curated pool's swap allowlist is completely defeated for any user who routes through `MetricOmmSimpleRouter`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the primary swap interface.
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the allowlist.

In either case the allowlist fails to enforce its intended policy. Non-allowlisted users gain unrestricted swap access to a pool whose LP funds are protected by the allowlist (e.g., KYC-gated, institutional-only, or rate-limited pools). This is a direct loss of the access-control guarantee that LPs relied on when depositing.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any user who wants to bypass the allowlist simply calls `exactInputSingle` / `exactInput` / `exactOutput` instead of calling `pool.swap` directly. No special privileges, flash loans, or contract deployment are required — only a standard router call.

---

### Recommendation

**Short term:** The pool should pass the original end-user's address as `sender` to extensions, not `msg.sender`. The router already stores the original caller in transient storage (`_getPayer()` / `_setNextCallbackContext`); it should forward that address as part of `extensionData` or through a dedicated field so the extension can check the real swapper identity.

**Long term:** Redesign the `beforeSwap` hook signature to include a separate `originator` field that the pool populates from a trusted source (e.g., a signed context or a dedicated router forwarding mechanism), so extensions can always distinguish the economic actor from the routing intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  swapExtension.setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
  swapExtension.setAllowedToSwap(pool, router, true)  // admin allowlists router so Alice can use it

Attack (Charlie, not allowlisted):
  1. Charlie calls router.exactInputSingle({pool: pool, recipient: charlie, ...})
  2. Router calls pool.swap(charlie, zeroForOne, amount, ...) — msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. Charlie's swap executes; allowlist is bypassed

Result:
  Charlie trades against LP funds on a pool that was supposed to be restricted to KYC'd users.
  Alice's deposit is exposed to an unrestricted counterparty the pool admin never intended to allow.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-19)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
