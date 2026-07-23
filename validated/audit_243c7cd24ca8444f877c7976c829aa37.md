Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router identity instead of end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. Any pool admin who allowlists the router (required for any allowed user to use the router) simultaneously grants every user — including explicitly disallowed ones — the ability to bypass the allowlist by routing through the router.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap` (L37):**

The check is `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded from the pool. [1](#0-0) 

**Pool populates `sender` with its own `msg.sender` (L230-231):**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. When the router calls `pool.swap(...)`, `msg.sender` to the pool is the router contract, so `sender = router`. [2](#0-1) 

**`ExtensionCalling._beforeSwap` passes `sender` through unchanged (L149-177):**

There is no mechanism to thread the original end-user identity. The extension only ever sees the immediate pool caller. [3](#0-2) 

**Router calls `pool.swap` directly as `msg.sender` (L72-80):**

`exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` with no mechanism to forward the original caller's identity into the extension path. [4](#0-3) 

**The dilemma this creates:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowed users cannot use the router at all |
| **Allowlist the router** | Every user, including disallowed ones, bypasses the allowlist via the router |

No configuration simultaneously permits router-mediated swaps for allowed users and blocks disallowed users.

**Concrete bypass path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, calls `setAllowedToSwap(pool, user1, true)` and `setAllowedToSwap(pool, router, true)`.
2. Disallowed `user2` calls `router.exactInputSingle(pool, ...)`.
3. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool is `router`.
4. Pool calls `extension.beforeSwap(sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. `user2`'s swap executes on the curated pool.

## Impact Explanation

The swap allowlist — the primary access-control mechanism for curated pools — is rendered ineffective for any user routing through the supported periphery. An unprivileged actor bypasses the pool admin's explicit allowlist policy without any special permissions. This is a direct admin-boundary break: the policy the admin configured is silently voided on the router path, which is the standard public entrypoint for swaps.

## Likelihood Explanation

Medium-High. Any pool that (a) deploys `SwapAllowlistExtension` to restrict swappers and (b) needs to support router-mediated swaps for those allowed users must allowlist the router. This is the expected operational pattern for curated pools integrating with the periphery. The bypass requires no privileged access, no special tokens, and no unusual state — only a standard router call.

## Recommendation

The extension must check the economically relevant actor, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity**: Have the router encode the end-user address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. The extension must also verify that `msg.sender` (the pool) is a trusted factory-deployed pool so the forwarded identity cannot be spoofed by a direct caller.

2. **Separate `originator` parameter**: Add an `originator` field to the pool's swap interface that the router populates with `msg.sender` before calling the pool, and thread it through `_beforeSwap` alongside `sender`.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists user1 and the router
ext.setAllowedToSwap(pool, user1, true);
ext.setAllowedToSwap(pool, address(router), true); // required for user1 to use router

// Attack: disallowed user2 routes through the router
vm.prank(user2);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: user2,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router, allowedSwapper[pool][router]=true
// user2 swaps on the curated pool despite not being allowlisted
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
