The vulnerability is real. Let me trace the exact code path before writing the finding.

The key is in `MetricOmmPool.swap()`: [1](#0-0) 

`msg.sender` (the router) is passed as `sender` to `_beforeSwap`, which then calls `SwapAllowlistExtension.beforeSwap` with `sender = router`.

In the extension, the check is: [2](#0-1) 

`msg.sender` here is the pool (the extension's caller), and `sender` is the router. So the check resolves to `allowedSwapper[pool][router]` — which is `true` — and the swap passes, even though the original EOA is not allowlisted.

---

### Title
Swap Allowlist Bypassed via Allowlisted Router Intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a router is allowlisted and any user calls through it, the extension sees the router as the swapper and passes the check, completely bypassing per-user allowlist enforcement.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
  msg.sender,   // <-- immediate caller, e.g. the router
  recipient,
  ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is the router. If `allowedSwapper[pool][router] = true`, the check passes regardless of who called the router. There is no mechanism to propagate or verify the original transaction initiator (`tx.origin` or a signed parameter).

The attack path:
1. Pool admin allowlists `router` but not `attacker`.
2. `attacker` calls `router.exactInputSingle(pool = target, ...)`.
3. Router calls `pool.swap(recipient = attacker, ...)` — `msg.sender` is `router`.
4. Pool calls `SwapAllowlistExtension.beforeSwap(sender = router, ...)`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `attacker` receives output tokens despite never being allowlisted.

### Impact Explanation
The swap allowlist's core invariant — that only individually approved addresses may swap — is completely broken. Any user who can call an allowlisted router (a public contract by design) can execute swaps against a restricted pool. This enables unauthorized token outflows from the pool, directly impacting LP principal and pool token balances. Impact is **High**.

### Likelihood Explanation
Routers are public, permissionless contracts. Any allowlisted router (e.g., a Metric periphery router) serves as a universal bypass for every non-allowlisted address. No special privileges or setup are required beyond knowing the router address. Likelihood is **High**.

### Recommendation
The extension must check the original transaction initiator, not the immediate pool caller. Options:

1. **Pass `tx.origin` as an additional parameter** in `beforeSwap` and check it alongside `sender`. This is the simplest fix but has known limitations with smart contract wallets.
2. **Require routers to forward the original caller** via `extensionData` and verify it with a signature or trusted forwarder pattern.
3. **Check both `sender` and `tx.origin`**: require both to be allowlisted, so a router can only be used by allowlisted EOAs.
4. **Disallow router-style intermediaries** by requiring `msg.sender == tx.origin` in the pool's `swap()` entry point when a swap allowlist extension is active.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: allowlist the router, NOT the attacker
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted

    // Attacker calls through the allowlisted router
    vm.prank(attacker);
    router.exactInputSingle(
        IRouter.ExactInputSingleParams({
            pool: address(pool),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: attacker,
            ...
        })
    );

    // Assert: swap succeeded despite attacker not being allowlisted
    // token1 balance of attacker increased
    assertGt(token1.balanceOf(attacker), 0);
}
```

The test will pass (no revert), demonstrating that the allowlist is bypassed. The extension sees `sender = router` (allowlisted) and never inspects the true initiator.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
