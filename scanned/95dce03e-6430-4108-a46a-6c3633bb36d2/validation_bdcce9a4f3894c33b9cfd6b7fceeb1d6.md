### Title
SwapAllowlistExtension Bypassed via MetricOmmSimpleRouter — Unauthorized Users Can Swap on Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, not the end user. If the router is allowlisted (which it must be for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the public router contract.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the first argument — the direct caller of `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is now the router address. The allowlist check becomes `allowedSwapper[pool][router]` — checking whether the router is allowed, not the end user.

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd users, institutional counterparties).
2. Pool admin allowlists the router address so that legitimate allowlisted users can use the router.
3. Any unauthorized user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
4. The pool sees `sender = router`, the allowlist check passes (`allowedSwapper[pool][router] == true`), and the swap executes.
5. The per-user allowlist is completely bypassed.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, substituting the router for the end user as `sender`.

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for pools to restrict swap access to specific counterparties. A complete bypass of this guard means:

- Unauthorized users can execute swaps against oracle-priced liquidity on pools intended to be restricted.
- LP assets are exposed to unrestricted trading, defeating the pool admin's access control intent.
- Any pool relying on the allowlist for risk management (e.g., gating to known counterparties, regulatory compliance) is rendered unprotected.

This constitutes broken core pool functionality with direct exposure of LP principal to unauthorized actors.

### Likelihood Explanation

Likelihood is **High**:
- The bypass requires no special privileges — any user with access to the public `MetricOmmSimpleRouter` can exploit it.
- The router is a standard, publicly deployed periphery contract.
- For the allowlist to be useful at all with router support, the admin must allowlist the router, which immediately opens the bypass to all users.
- The admin has no way to simultaneously allow router-mediated swaps for legitimate users and block unauthorized users, because the router collapses all user identities into a single address.

### Recommendation

The `SwapAllowlistExtension` should check the economically relevant actor — the end user — not the intermediate contract. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires trust that the router correctly encodes the user, which is fragile.

2. **Check `sender` at the router level before calling the pool:** The router reads the allowlist and reverts before forwarding to the pool if the user is not allowed.

3. **Preferred — check `tx.origin` or require direct pool calls for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not allowlist router contracts, and that allowlisted users must call the pool directly. The extension's NatSpec and admin tooling should enforce this constraint.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `allowedUser` is allowlisted
// pool admin also allowlists the router so allowedUser can use it

address allowedUser   = makeAddr("allowedUser");
address attackerUser  = makeAddr("attackerUser");

// Admin allowlists the router (required for router-mediated swaps)
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Admin allowlists the legitimate user
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), allowedUser, true);

// Attacker (not allowlisted) routes through the router — succeeds
vm.prank(attackerUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attackerUser,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ↑ Does NOT revert — allowedSwapper[pool][router] == true
// The per-user allowlist is bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
