### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual end-user. If the router is allowlisted (which is required for any allowlisted user to use the router), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is whatever address the pool received as `msg.sender` when `swap()` was called. In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the hook:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router, not the end-user
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So the allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The asymmetry with `DepositAllowlistExtension` is the clearest evidence of the bug.** The deposit allowlist correctly checks `owner` — the actual position owner passed explicitly through the call chain — which remains the real user even when the `MetricOmmPoolLiquidityAdder` is used:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The swap allowlist has no equivalent "actual user" field to check — it only sees `sender = router`.

---

### Impact Explanation

The pool admin is forced into an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| **No** | All router-mediated swaps revert — allowlisted users cannot use the router at all |
| **Yes** | Every user, allowlisted or not, can bypass the gate by routing through the router |

If the router is allowlisted (the only way to let any allowlisted user use the router), an unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` on the restricted pool. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and permits the swap. The allowlist is completely ineffective for router-mediated swaps. This is an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path, allowing unauthorized parties to execute swaps on pools intended to be restricted.

---

### Likelihood Explanation

This surfaces in any deployment where:
1. A pool is configured with `SwapAllowlistExtension` to restrict swap access.
2. The pool admin allowlists the router so that legitimate allowlisted users can trade via the standard periphery.

Both conditions are expected in normal production use of the extension. The bypass requires no special privileges — any address can call `MetricOmmSimpleRouter`.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct caller. Since the pool's `beforeSwap` hook does not carry a separate "originating user" field, the recommended fix is to require the actual user's identity to be passed in `extensionData` (e.g., as a signed attestation or a plain address that the router is trusted to populate). Alternatively, the router can be modified to include the original `msg.sender` in `extensionData`, and the extension can verify it against the allowlist instead of `sender`.

As a minimal fix consistent with the existing `DepositAllowlistExtension` pattern, the extension should check an identity that is invariant to router indirection — analogous to how `owner` is used in the deposit path.

---

### Proof of Concept

```solidity
// Pool admin sets up SwapAllowlistExtension: only Alice is allowed to swap.
allowlistExt.setAllowedToSwap(pool, alice, true);

// Pool admin also allowlists the router so Alice can use it.
allowlistExt.setAllowedToSwap(pool, address(router), true);

// Mallory (not allowlisted) calls the router directly.
// The router calls pool.swap(msg.sender=router, ...).
// beforeSwap receives sender=router.
// allowedSwapper[pool][router] == true → swap succeeds.
vm.prank(mallory);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: mallory,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Mallory's swap succeeds despite not being on the allowlist.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
