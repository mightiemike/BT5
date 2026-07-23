Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router contract as `msg.sender`, which is then forwarded verbatim as `sender` to the extension. If the pool admin allowlists the router address to enable router-mediated swaps, every user on the network can bypass the allowlist entirely by calling the router, defeating the curation guarantee for LPs.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract address, not the end user.

**Step 2 — ExtensionCalling forwards that address verbatim:**
`ExtensionCalling._beforeSwap` (lines 149–177) encodes `sender` directly into the ABI call to every configured extension with no substitution of the original user.

**Step 3 — SwapAllowlistExtension checks the wrong actor:**
`SwapAllowlistExtension.beforeSwap` at line 37 evaluates `allowedSwapper[msg.sender][sender]`, which resolves to `allowedSwapper[pool][router]`. The router address is what is evaluated, not the end user's address.

**Step 4 — Router never substitutes the real caller:**
`MetricOmmSimpleRouter.exactInputSingle` (lines 72–80) calls `pool.swap(params.recipient, ...)` directly. The router's own address becomes `msg.sender` inside the pool. The same pattern holds for `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165).

**The two broken invariants:**

| Scenario | Effect |
|---|---|
| Pool admin allowlists the router (to enable router-mediated swaps) | Every user on the network can swap — allowlist is completely bypassed |
| Pool admin allowlists individual user addresses but not the router | Allowlisted users cannot use the router at all; they must call the pool directly |

The `extensionData` bytes forwarded by the router are user-controlled and not decoded by `SwapAllowlistExtension`, so there is no existing mechanism to recover the real caller.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a whitelist of addresses loses that protection entirely the moment the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool, draining liquidity at oracle-anchored prices. This constitutes broken core pool functionality with direct fund-impacting consequences for LPs who deposited under the assumption that only approved counterparties could trade. Severity: High.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed by the protocol. Any pool admin who configures a `SwapAllowlistExtension` and then allowlists the router — the natural step to make the pool usable via the standard periphery — immediately opens the bypass. The trigger requires no special privilege; any EOA calling the public router functions suffices. The condition is easily and repeatedly triggerable.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the intermediary contract. Two viable fixes:

1. **Router-level**: Have the router encode `msg.sender` into `extensionData` using a well-known prefix, and have `SwapAllowlistExtension` decode and verify it when the caller is a trusted router.
2. **Pool-level**: Add a separate `originalSender` field to the swap call that the pool populates from a trusted periphery registry, and pass that to extensions instead of `msg.sender`.

Option 2 is cleaner because it does not require extensions to trust user-supplied `extensionData`.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so that router-mediated swaps work
ext.setAllowedToSwap(pool, address(router), true);
// Alice is NOT on the allowlist
address alice = makeAddr("alice");

// Attack: Alice calls the router directly
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: alice,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds: pool.swap() passes msg.sender = router to _beforeSwap;
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
// Alice, who is not allowlisted, has successfully swapped on a curated pool.
```

The call chain is:
- `alice` → `MetricOmmSimpleRouter.exactInputSingle` (line 67) → `pool.swap(params.recipient, ...)` (line 72–80)
- Inside pool: `_beforeSwap(msg.sender=router, ...)` (line 231)
- Inside extension: `allowedSwapper[pool][router] == true` → check passes (line 37)
- Alice's swap executes despite not being on the allowlist.