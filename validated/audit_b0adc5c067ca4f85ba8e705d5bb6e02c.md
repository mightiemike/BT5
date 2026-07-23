Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` populates with `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (a natural operational step to enable router-mediated swaps for allowlisted users), every user — including those not on the allowlist — can bypass the guard by routing through the router, completely voiding the pool's curation policy.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`. When `MetricOmmSimpleRouter` is the caller, `msg.sender` = router address. [1](#0-0) 

**Step 2 — ExtensionCalling forwards `sender` verbatim:**
`_beforeSwap` in `ExtensionCalling.sol` encodes `sender` into the extension call without modification. [2](#0-1) 

**Step 3 — SwapAllowlistExtension checks the wrong actor:**
The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool (correct) and `sender` = router (wrong — should be the end user). [3](#0-2) 

**Step 4 — Router calls pool directly, no user identity forwarded:**
`exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` (the end user) into the pool's `sender` argument. [4](#0-3) 

**Contrast with DepositAllowlistExtension (not affected):**
`DepositAllowlistExtension.beforeAddLiquidity()` checks `owner`, which is an explicit argument correctly set to the position owner, not the routing intermediary. [5](#0-4) 

Existing guards are insufficient: there is no mechanism in the pool, router, or extension to distinguish the economic actor (end user) from the routing intermediary (router). The `extensionData` field is passed through but the extension does not decode it for identity verification.

## Impact Explanation
**Scenario A — Allowlist bypass (High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC'd addresses). The admin also allowlists the router so that allowlisted users can trade through the standard periphery interface — a natural and expected configuration. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`. The router calls `pool.swap()` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap executes. The allowlist is completely bypassed for all users. This constitutes broken core pool functionality (the allowlist invariant the pool was designed to enforce is silently voided) and an admin-boundary break where an unprivileged user bypasses a pool access control mechanism.

**Scenario B — Broken router path for allowlisted users (Medium):** If the admin does NOT allowlist the router, allowlisted users who attempt to trade through the router are blocked. The primary user-facing interface is unusable for the pool's intended participants.

## Likelihood Explanation
The trigger is a valid, unprivileged user action (calling the public router). The precondition — the admin allowlisting the router — is a reasonable operational step that any pool admin would take to make their curated pool usable through the standard periphery. The admin has no way to know this action opens the gate to all users, because the extension's documentation and interface imply it gates by swapper identity. The bug is latent in every `SwapAllowlistExtension`-protected pool that uses the router.

## Recommendation
The extension must check the end user's identity, not the immediate caller of `pool.swap()`. Two approaches:
1. **Preferred:** Require that swaps through the router pass the original user's address in `extensionData`, and have the extension decode and check that address. The router already forwards `extensionData` per-hop.
2. **Alternative:** The pool could forward the original user's address as a separate field (analogous to how `owner` is separate from `sender` in the liquidity path), so extensions can distinguish the economic actor from the routing intermediary.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin also allowlists the router so allowedUser can trade via periphery.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: bannedUser routes through the router.
vm.prank(bannedUser);
// bannedUser approves router for tokenIn, then:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bannedUser,
    tokenIn: address(token1),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → swap succeeds.
// bannedUser has bypassed the allowlist.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
