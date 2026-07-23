Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router, so the extension checks the router's allowlist status rather than the originating user's. This produces two fund-impacting outcomes: if the router is allowlisted to enable router-based swaps, every user bypasses the per-user gate; if the router is not allowlisted, allowlisted users cannot swap through the router at all.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly with no mechanism to forward the original user into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← user-supplied; no enforced original-sender encoding
  );
```

The pool therefore receives `msg.sender = router`, passes `sender = router` to the extension, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][originalUser]`. The same identity mismatch applies to `exactInput` (L99-125), `exactOutputSingle` (L130-147), and `exactOutput` (L154-188). Existing guards (deadline check, callback context validation) do not address the sender identity problem.

## Impact Explanation
**Scenario A — Router allowlisted (bypass):** A pool admin who allowlists the router to enable router-based swaps causes `allowedSwapper[pool][router] == true` to pass for every caller. Any non-allowlisted user routes through `MetricOmmSimpleRouter` and trades on the curated pool, defeating the access control and enabling unauthorized swaps against LP principal.

**Scenario B — Router not allowlisted (broken functionality):** Allowlisted users who attempt to swap through the router are blocked because the router address is not in the allowlist. The primary user-facing swap interface is unusable for the pool's intended participants, constituting broken core pool functionality and potentially stranding LP positions.

Both outcomes are fund-impacting: Scenario A enables unauthorized counterparties to drain LP value; Scenario B prevents legitimate swaps.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint for EOA users. Pool admins deploying allowlisted pools will naturally allowlist the router to enable user access, directly triggering Scenario A. No privileged access is required beyond a normal router call. The bypass is deterministic and repeatable every block.

## Recommendation
The extension must verify the economically relevant actor — the original user — not the intermediary. The cleanest fix is for the router to encode `msg.sender` as a prefix in `extensionData` for each hop, and for `SwapAllowlistExtension.beforeSwap` to decode the original caller from `extensionData` when `sender` is a recognized router address. Alternatively, the extension can maintain a trusted-router registry and, when `sender` is a router, read the original user from a standardized `extensionData` field.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only allowedUser is allowlisted
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// router is NOT allowlisted

// Step 1: allowedUser swaps directly — succeeds (sender = allowedUser)
vm.prank(allowedUser);
pool.swap(recipient, zeroForOne, amount, priceLimit, "", "");

// Step 2: admin allowlists router to "enable router usage"
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Step 3: bannedUser routes through router
// extension checks allowedSwapper[pool][router] == true → passes
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bannedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds — bannedUser bypasses the per-user allowlist
```