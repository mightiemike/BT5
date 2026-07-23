### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a gated pool), every user on the network can bypass the allowlist by calling the router.

---

### Finding Description

**Step 1 – Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the first argument to `_beforeSwap` is `msg.sender`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the end user.

**Step 2 – `SwapAllowlistExtension` checks that router address.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct), and `sender` is the router address (wrong actor).

**Step 3 – Router calls pool with no user-identity forwarding.**

`exactInputSingle`, `exactInput`, and `exactOutputSingle` all call `pool.swap(...)` directly with no mechanism to forward the original caller's address into `extensionData` or any other field: [3](#0-2) 

**Resulting invariant break:**

| Admin intent | What the extension actually checks | Outcome |
|---|---|---|
| Allowlist specific users | `allowedSwapper[pool][router]` | Router must be allowlisted for any user to use it; once allowlisted, **all** users bypass the gate |
| Block all non-allowlisted users | Router address not in allowlist | **All** router users are blocked, even those the admin intended to allow |

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) provides **zero enforcement** against any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege: they simply call the public router. The pool admin cannot simultaneously (a) allow their intended users to use the router and (b) block unintended users, because the extension cannot distinguish between them — it only sees the router's address.

This breaks the core purpose of the `SwapAllowlistExtension` and constitutes a **curation failure** on any pool that relies on it for access control.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint in the periphery.
- Any user who discovers the allowlist can trivially route through the router instead of calling the pool directly.
- No special knowledge, capital, or timing is required — a single public call suffices.
- The pool admin has no on-chain mechanism to close this gap without removing the router from the allowlist (which breaks all router users) or disabling the extension entirely.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender` for router flows, or add a dedicated `originalSender` field**: The pool interface could be extended to carry the original initiator separately from the callback payer, so extensions always see the true economic actor regardless of routing path.

Until fixed, pools that require strict swap access control must instruct users to call `MetricOmmPool.swap` directly (implementing `IMetricOmmSwapCallback` themselves) and must **not** allowlist the router address.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin intends to allow only `alice` to swap.
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Bob is NOT allowlisted.
// Direct call from Bob reverts correctly:
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// But Bob routes through the router.
// Admin must have allowlisted the router for alice to use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Now Bob bypasses the allowlist entirely:
vm.prank(bob);
// This succeeds — extension sees sender=router, which is allowlisted.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob received output tokens despite not being on the allowlist.
```

The extension checks `allowedSwapper[pool][router]` (true) and passes, even though `bob` is the economic actor performing the swap. [4](#0-3) [5](#0-4) [6](#0-5)

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
